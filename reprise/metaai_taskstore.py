#!/usr/bin/env python3
# metaai_taskstore.py — cœur commun du pont Meta AI (TaskStore SQLite, WAL)
#
# Seul état du système ; tous les adaptateurs sont sans état :
#   - watcher metaaid (metaai_bridge_watch.py) : record_result des réponses Meta AI
#   - pont MCP (metaai_mcp_server.py) : enqueue / dispatch / get_task / submit_result
#
# Schéma : tasks(id PK, type, payload JSON, status, result JSON, result_source,
#                created_at, dispatched_at, answered_at)
# Cycle   : pending → dispatched → answered
#           answered_orphan = résultat reçu pour une tâche hors store (créée à la main)

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_task_id() -> str:
    return "tache-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


# Issue #277 — contrat de consignes d'échange : chaque inbox matérialisée par
# dispatch_write_inbox embarque un bloc `consignes` autoportant (où lire la
# tâche, où écrire la réponse, format attendu, task_id à reporter) pour que
# la session Meta AI puisse répondre sans aucun contexte externe. Versionné :
# toute évolution du protocole incrémente CONSIGNES_VERSION (test de
# régression : présence + contenu dans test_metaai_taskstore.py et
# test_metaai_consignes.py).
CONSIGNES_VERSION = "1"
CONSIGNES_ECHANGE = (
    "Protocole d'échange du pont Reprise <-> Meta AI (version " + CONSIGNES_VERSION + ") :\n"
    "1. Lis la tâche du fichier inbox/<task_id>.json du dossier partagé "
    "(le champ `question` porte la demande ; les autres champs sont du contexte).\n"
    "2. Calcule la réponse à la question posée.\n"
    "3. Écris un fichier outbox/<task_id>.txt dans le dossier partagé, contenant "
    "exactement trois lignes clé/valeur (pas de JSON) :\n"
    "   task_id: <le task_id exact de la tâche>\n"
    "   reponse: <ta réponse>\n"
    "   confiance: <nombre entre 0 et 1>\n"
    "4. Si la tâche embarque des pièces (`pieces`) ou pointe un chemin du dossier "
    "partagé, base ton avis sur ce contenu borné avant de répondre ; si une pièce "
    "manque, dis-le dans `reponse` et baisse `confiance`.\n"
    "5. Reporte le task_id SANS le modifier : c'est la seule clé qui relie ta "
    "réponse à la tâche ; un task_id faux ou absent = réponse perdue."
)


# Issue #277 — circuit du harness DEMANDEUR : le reçu de reprise_delegate est
# une admission, pas une réponse. Les harnesses pressés lisent status=finished
# comme une réponse finale et ne rappellent jamais wait_result — délégation
# perdue en silence. Le texte versionné ci-dessous est attaché à CHAQUE reçu
# metaai (champ `circuit`) : mode d'emploi autoportant, testé.
CIRCUIT_DEMANDEUR_VERSION = "1"
CIRCUIT_DEMANDEUR = (
    "Circuit demandeur Meta AI (version " + CIRCUIT_DEMANDEUR_VERSION + ") :\n"
    "1. Ce reçu est une ADMISSION, pas une réponse : response=null et "
    "admission_only=true sont normaux — ne pas les traiter comme un résultat.\n"
    "2. Appeler l'outil wait_result avec request_key=<le request_key de ce reçu> "
    "et timeout_s (<= 600) jusqu'à status=finished ; sur timeout, rappeler "
    "wait_result avec la MÊME clé (l'attente est réentrant, pas un re-dispatch).\n"
    "3. La réponse finale vit dans result.reponse ; sa fiabilité annoncée est "
    "result.confiance (0 à 1) — une confiance basse = à re-vérifier.\n"
    "4. Ne JAMAIS re-dispatcher le même request_key : une relance = nouvelle clé "
    "avec retry_of (sinon garde review_started).\n"
    "5. task_id identifie la tâche dans le pont (get_status) ; ne pas le modifier."
)


class TaskStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        # Le store est partagé entre processus (ponts MCP) et threads (watcher metaaid) :
        # WAL + busy_timeout évitent les OperationalError « database is locked ».
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL DEFAULT 'question',
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT,
                    result_source TEXT,
                    created_at TEXT,
                    dispatched_at TEXT,
                    answered_at TEXT
                )"""
            )
            self._conn.commit()

    def enqueue(self, type_: str, payload: dict, task_id: Optional[str] = None) -> str:
        tid = task_id or new_task_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, type, payload, status, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (tid, type_, json.dumps(payload, ensure_ascii=False), "pending", now_iso()),
            )
            # Un id existant est laissé intact (une tâche answered n'est pas effacée) :
            # pour réessayer, utiliser un nouvel id.
            self._conn.commit()
        return tid

    def claim_next(self, task_id: Optional[str] = None) -> Optional[dict]:
        """Usage A : réserver une tâche pour l'agent appelant (pending → dispatched).
        Sans task_id : la plus ancienne pending.
        La réservation est atomique ENTRE processus : l'UPDATE porte la garde
        `AND status='pending'` et n'aboutit que si elle réserve exactement une
        ligne (rowcount=1) — deux ponts concurrents ne peuvent pas réserver la
        même tâche. Le RLock ne protège que les threads du process courant."""
        with self._lock:
            if task_id:
                cur = self._conn.execute(
                    "UPDATE tasks SET status='dispatched', dispatched_at=? "
                    "WHERE id=? AND status='pending'",
                    (now_iso(), task_id),
                )
                if cur.rowcount != 1:
                    return None
                tid = task_id
            else:
                row = self._conn.execute(
                    "SELECT id FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if not row:
                    return None
                cur = self._conn.execute(
                    "UPDATE tasks SET status='dispatched', dispatched_at=? "
                    "WHERE id=? AND status='pending'",
                    (now_iso(), row["id"]),
                )
                if cur.rowcount != 1:  # réservé entre-temps par un autre processus
                    return None
                tid = row["id"]
            self._conn.commit()
            return dict(self._conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())

    def dispatch_write_inbox(self, task: dict, inbox_dir) -> str:
        """Usage B : matérialiser la tâche pour Meta AI (inbox/<id>.json, format lu validé).

        Issue #277 : l'inbox embarque systématiquement le bloc `consignes`
        (version + texte autoportant) — la session Meta AI doit pouvoir boucler
        l'échange sans contexte externe. Le contrat est IMPOSÉ : un bloc
        appelant obsolète (version != CONSIGNES_VERSION) est remplacé par la
        version courante, jamais rétrogradé (test de régression :
        test_metaai_consignes.py).
        """
        payload = json.loads(task["payload"]) if task["payload"] else {}
        payload.setdefault("task_id", task["id"])
        payload.setdefault("type", task["type"])
        consignes = payload.get("consignes")
        if not isinstance(consignes, dict) or consignes.get("version") != CONSIGNES_VERSION:
            payload["consignes"] = {"version": CONSIGNES_VERSION,
                                    "texte": CONSIGNES_ECHANGE}
        inbox = Path(inbox_dir).expanduser()
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / f"{task['id']}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)  # rename atomique sur APFS (côté serveur réel)
        return str(target)

    def record_result(self, task_id: str, result: dict, raw: str, source: str) -> str:
        """Enregistrer un résultat (watcher Meta AI ou submit_result MCP)."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                status = "answered" if row["status"] in ("pending", "dispatched") else row["status"]
                self._conn.execute(
                    "UPDATE tasks SET status=?, result=?, result_source=?, answered_at=? WHERE id=?",
                    (status, json.dumps(result, ensure_ascii=False), source, now_iso(), task_id),
                )
            else:
                status = "answered_orphan"
                self._conn.execute(
                    """INSERT INTO tasks (id, type, payload, status, result, result_source, answered_at)
                       VALUES (?, 'orphan', '{}', ?, ?, ?, ?)""",
                    (task_id, status, json.dumps(result, ensure_ascii=False), source, now_iso()),
                )
            self._conn.commit()
        return status

    def fail(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (task_id,))
            self._conn.commit()

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self, status: Optional[str] = None) -> list:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY created_at", (status,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]


def summary(task: dict) -> dict:
    """Projection compacte d'une tâche pour les réponses MCP."""
    if not task:
        return {"task_id": None}
    return {
        "task_id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "payload": json.loads(task["payload"]) if task["payload"] else {},
        "result": json.loads(task["result"]) if task["result"] else None,
        "result_source": task["result_source"],
    }
