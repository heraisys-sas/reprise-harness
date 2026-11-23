import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import metaai_ui_dispatch as uid
import metaai_bridge_watch as bridge


class FakeMetaAI:
    """Socket de contrôle de copie de laboratoire : enregistre insert/send."""

    def __init__(self, path):
        self.path = path
        self.inserted = []
        self.sent = 0
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        srv.listen(4)
        self.srv = srv
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        buf = b""
        while b"\n" not in buf:
            c = conn.recv(4096)
            if not c:
                conn.close()
                return
            buf += c
        try:
            payload = json.loads(buf.decode().strip())
        except Exception:
            payload = {}
        cmd = payload.get("cmd")
        if cmd == "insert":
            self.inserted.append(payload.get("text"))
            reply = {"ok": True}
        elif cmd == "send":
            self.sent += 1
            reply = {"ok": True, "note": "insertNewline"}
        elif cmd == "len":
            reply = {"ok": True, "len": 0 if self.sent else 12}
        elif cmd == "ping":
            reply = {"ok": True, "pid": 1}
        else:
            reply = {"ok": False}
        conn.sendall((json.dumps(reply) + "\n").encode())
        conn.close()


class UiDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "inbox").mkdir()
        self.sock_path = self.root / "bridge.sock"
        self.fake = FakeMetaAI(self.sock_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_mentions_inbox_outbox_and_id(self):
        p = uid.build_prompt("tache-1")
        self.assertIn("inbox/tache-1.json", p)
        self.assertIn("outbox/tache-1.txt", p)
        self.assertIn("task_id: tache-1", p)

    def test_dispatch_injects_marks_and_is_idempotent(self):
        (self.root / "inbox" / "tache-1.json").write_text(
            json.dumps({"task_id": "tache-1", "question": "2+2"}), encoding="utf-8")
        self.assertEqual(uid.dispatch_pending(self.root, self.sock_path), 1)
        self.assertEqual(len(self.fake.inserted), 1)
        self.assertIn("tache-1", self.fake.inserted[0])
        self.assertEqual(self.fake.sent, 1)
        self.assertTrue((self.root / ".ui-state" / "tache-1.json").exists())
        self.assertEqual(uid.dispatch_pending(self.root, self.sock_path), 0)
        self.assertEqual(self.fake.sent, 1)

    def test_apple_double_ignored(self):
        (self.root / "inbox" / "._tache-x.json").write_text("{}", encoding="utf-8")
        self.assertEqual(uid.dispatch_pending(self.root, self.sock_path), 0)

    def test_ping_reflects_socket(self):
        self.assertTrue(uid.ping(self.sock_path))
        self.assertFalse(uid.ping(self.root / "absente.sock"))

    def test_ensure_app_returns_true_when_socket_answers(self):
        self.assertTrue(uid.ensure_app(self.sock_path, self.root / "nimporte" / "binaire"))

    def test_watch_loop_once_drives_ui(self):
        (self.root / "inbox" / "tache-9.json").write_text(
            json.dumps({"task_id": "tache-9"}), encoding="utf-8")
        bridge.watch_loop(self.root, once=True, ui={"socket_path": str(self.sock_path)})
        self.assertTrue((self.root / ".ui-state" / "tache-9.json").exists())
        self.assertEqual(self.fake.sent, 1)

    def test_watch_loop_without_ui_keeps_historic_behavior(self):
        (self.root / "inbox" / "tache-0.json").write_text(
            json.dumps({"task_id": "tache-0"}), encoding="utf-8")
        bridge.watch_loop(self.root, once=True)
        self.assertFalse((self.root / ".ui-state" / "tache-0.json").exists())


if __name__ == "__main__":
    unittest.main()
