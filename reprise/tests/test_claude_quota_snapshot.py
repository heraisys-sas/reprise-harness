"""Schéma du snapshot claude_quota lu par le Dashboard (#131)."""

import sqlite3
import tempfile
import unittest

import claude_quota


class ClaudeQuotaSnapshotSchemaTests(unittest.TestCase):
    def test_status_events_expose_dashboard_fields(self):
        db = sqlite3.connect(tempfile.mktemp())
        claude_quota.initialize(db)
        db.execute(
            "INSERT INTO claude_quota_resumes (event_key, chat_id, observed_at, due,"
            " fingerprint, pause_hash, status) VALUES ('k1','chat-x',1,2,'fp','ph','waiting')"
        )
        events = claude_quota.status(db)
        self.assertEqual(1, len(events))
        for field in ("event_key", "status", "chat_id", "due"):
            self.assertIn(field, events[0])


if __name__ == "__main__":
    unittest.main()
