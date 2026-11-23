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


class TaskStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
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
                "INSERT OR REPLACE INTO tasks (id, type, payload, status, created_at) VALUES (?,?,?,?,?)",
                (tid, type_, json.dumps(payload, ensure_ascii=False), "pending", now_iso()),
            )
            self._conn.commit()
        return tid

    def claim_next(self, task_id: Optional[str] = None) -> Optional[dict]:
        """Usage A : réserver une tâche pour l'agent appelant (pending → dispatched).
        Sans task_id : la plus ancienne pending."""
        with self._lock:
            if task_id:
                row = self._conn.execute(
                    "SELECT * FROM tasks WHERE id=? AND status='pending'", (task_id,)
                ).fetchone()
                if not row:
                    return None
                self._conn.execute(
                    "UPDATE tasks SET status='dispatched', dispatched_at=? WHERE id=?",
                    (now_iso(), task_id),
                )
            else:
                row = self._conn.execute(
                    "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if not row:
                    return None
                self._conn.execute(
                    "UPDATE tasks SET status='dispatched', dispatched_at=? WHERE id=?",
                    (now_iso(), row["id"]),
                )
            self._conn.commit()
            return dict(self._conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def dispatch_write_inbox(self, task: dict, inbox_dir) -> str:
        """Usage B : matérialiser la tâche pour Meta AI (inbox/<id>.json, format lu validé)."""
        payload = json.loads(task["payload"]) if task["payload"] else {}
        payload.setdefault("task_id", task["id"])
        payload.setdefault("type", task["type"])
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
