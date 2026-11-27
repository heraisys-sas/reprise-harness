#!/usr/bin/env python3
# metaai_mcp_server.py — pont MCP stdio sur le TaskStore (cœur partagé avec metaaid)
#
# Transport : JSON-RPC 2.0, messages JSON délimités par newline sur stdin/stdout (MCP stdio).
# Aucune dépendance hors Python standard + metaai_taskstore.
#
# Outils :
#   enqueue_task   — créer une tâche pending (Reprise → file)
#   dispatch_task  — réserver une tâche et la matérialiser en inbox/<id>.json (→ Meta AI)
#   get_task       — réserver une tâche pour l'agent appelant lui-même (usage Claude Desktop,
#                    SANS écriture inbox — sinon Meta AI traiterait en double)
#   submit_result  — enregistrer un résultat (usage agent MCP exécutant)
#   get_status     — tâche complète par id
#   list_tasks     — liste filtrée par statut
#
# Config : --config <chemin connexion> (défaut ~/Library/Application Support/Reprise/
#          connections/metaai.json ; bridge_root → inbox pour dispatch_task (repli
#          legacy dav_root) ; taskstore → SQLite).
# L'argument --config permet aussi aux tests de tourner sur des répertoires temporaires.

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metaai_taskstore import TaskStore, summary  # noqa: E402

CONFIG_DEFAULTS = {
    "bridge_root": "~/Library/Application Support/Reprise/metaaid-bridge",
    "taskstore": "~/Library/Application Support/Reprise/metaai-tasks.sqlite",
}

STORE = None  # TaskStore, initialisé dans main()
INBOX = None  # Path, initialisé dans main()


def load_config(path) -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    if path:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if "dav_root" in raw:
            raise ValueError("clé dav_root supprimée (#276) ; la renommer bridge_root dans " + str(path))
        cfg.update({k: v for k, v in raw.items()
                    if k in ("bridge_root", "taskstore") and v is not None})
    cfg["bridge_root"] = str(Path(cfg["bridge_root"]).expanduser())
    cfg["taskstore"] = str(Path(cfg["taskstore"]).expanduser())
    return cfg


TOOLS = [
    {
        "name": "enqueue_task",
        "description": "Créer une tâche pending dans le TaskStore (à dispatcher ensuite vers Meta AI ou à traiter soi-même via get_task).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "type de tâche, ex. question"},
                "payload": {"type": "object", "description": "contenu de la tâche (question, format_attendu, …)"},
                "task_id": {"type": "string", "description": "id optionnel (sinon généré)"},
            },
            "required": ["type", "payload"],
        },
    },
    {
        "name": "dispatch_task",
        "description": "Réserver une tâche pending et écrire inbox/<id>.json pour Meta AI (pont fichiers). Sans task_id : la plus ancienne pending.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
        },
    },
    {
        "name": "get_task",
        "description": "Réserver une tâche pending pour l'agent appelant LUI-MÊME (sans écriture inbox — Meta AI ne doit pas la traiter en double).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_result",
        "description": "Enregistrer le résultat d'une tâche revendiquée par l'agent appelant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "result": {"type": "object"},
            },
            "required": ["task_id", "result"],
        },
    },
    {
        "name": "get_status",
        "description": "Tâche complète par id (statut, payload, résultat).",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    },
    {
        "name": "list_tasks",
        "description": "Liste des tâches, filtrable par statut (pending/dispatched/answered/…).",
        "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
    },
]


def tool_enqueue(args: dict) -> dict:
    tid = STORE.enqueue(args.get("type", "question"), args.get("payload") or {}, args.get("task_id"))
    return {"task_id": tid, "status": "pending"}


def tool_dispatch(args: dict) -> dict:
    task = STORE.claim_next(args.get("task_id"))
    if not task:
        return {"task_id": None, "note": "aucune tâche pending"}
    inbox_path = STORE.dispatch_write_inbox(task, INBOX)
    return {"task_id": task["id"], "status": "dispatched", "inbox_path": inbox_path}


def tool_get_task(args: dict) -> dict:
    task = STORE.claim_next()
    return summary(task) if task else {"task_id": None, "note": "aucune tâche pending"}


def tool_submit_result(args: dict) -> dict:
    status = STORE.record_result(args["task_id"], args.get("result") or {}, json.dumps(args.get("result") or {}), "mcp")
    return {"task_id": args["task_id"], "status": status}


def tool_get_status(args: dict) -> dict:
    row = STORE.get(args["task_id"])
    # summary(None) est véridique ({"task_id": None}) : la branche unknown doit
    # être choisie explicitement, sinon une tâche inconnue est indistinguable.
    return summary(row) if row is not None else {"task_id": args["task_id"], "status": "unknown"}


def tool_list_tasks(args: dict) -> dict:
    return {"tasks": [summary(t) for t in STORE.list_tasks(args.get("status"))]}


HANDLERS = {
    "enqueue_task": tool_enqueue,
    "dispatch_task": tool_dispatch,
    "get_task": tool_get_task,
    "submit_result": tool_submit_result,
    "get_status": tool_get_status,
    "list_tasks": tool_list_tasks,
}


def respond(msg_id, result) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False), flush=True)


def respond_error(msg_id, code: int, message: str) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}), flush=True)


def main() -> None:
    global STORE, INBOX
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="chemin de la connexion metaai (JSON)")
    parsed = parser.parse_args()
    cfg = load_config(parsed.config)
    STORE = TaskStore(cfg["taskstore"])
    INBOX = Path(cfg["bridge_root"]) / "inbox"
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue  # notification (initialized…) : rien à répondre
        method = msg.get("method")
        mid = msg["id"]
        if method == "initialize":
            respond(mid, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "reprise-metaai", "version": "0.1.0"},
            })
        elif method == "ping":
            respond(mid, {})
        elif method == "tools/list":
            respond(mid, {"tools": TOOLS})
        elif method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            args = (msg.get("params") or {}).get("arguments") or {}
            handler = HANDLERS.get(name)
            if not handler:
                respond_error(mid, -32601, f"outil inconnu: {name}")
                continue
            try:
                respond(mid, {"content": [{"type": "text", "text": json.dumps(handler(args), ensure_ascii=False)}], "isError": False})
            except Exception as e:
                respond(mid, {"content": [{"type": "text", "text": f"erreur: {e}"}], "isError": True})
        else:
            respond_error(mid, -32601, f"méthode inconnue: {method}")


if __name__ == "__main__":
    main()
