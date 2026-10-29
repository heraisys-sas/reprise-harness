"""CDP pipe transport for Claude resumption after quota.

Messages are JSON separated by NUL ('\0'). In --remote-debugging-pipe mode
Chrome reads commands from its fd 3 and writes responses to its fd 4
(devtools_pipe_handler.cc, chromium main). The transport attaches to the
browser target with flatten and sends the returned sessionId on every
subsequent message: Target.getTargets, Target.attachToTarget {flatten:true},
Runtime.evaluate (returnByValue + awaitPromise), Input.insertText. No domain
enable call is ever sent.
"""
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# PipeTransport
# ---------------------------------------------------------------------------

class PipeTransport:
    """Speak CDP over a pair of file descriptors framed by NUL."""

    def __init__(self, child_proc, read_fd, write_fd):
        """Wrap a Popen plus the parent's pipe ends, passed as parameters.

        In --remote-debugging-pipe mode Chrome reads commands from fd 3 and
        writes responses to fd 4. *read_fd* reads what Chrome writes on fd 4,
        *write_fd* feeds what Chrome reads on fd 3: the parent dup2's
        rA→3 (commands) and wB→4 (responses) around Popen, keeps wA/rB for
        itself and passes (3, 4) via pass_fds — no fd is hard-coded here.
        """
        self._proc = child_proc
        self._id = 0
        self._session_id = None
        self._reader = os.fdopen(read_fd, 'rb')
        self._writer = os.fdopen(write_fd, 'wb')

    # -- framing -------------------------------------------------------------

    def _send(self, message):
        """Encode *message* as JSON + NUL delimiter."""
        payload = json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\0'
        self._writer.write(payload)
        self._writer.flush()

    def _receive(self):
        """Read one NUL-delimited JSON frame. Skip notifications (no 'id').

        Reads byte-by-byte (buffered read(1)) until a NUL is found, avoiding
        blocking up to 64 KiB on buffered readers.
        """
        while True:
            raw = bytearray()
            while True:
                chunk = self._reader.read(1)
                if not chunk:
                    raise EOFError("Pipe closed by Chrome")
                if chunk == b'\0':
                    break
                raw.extend(chunk)
            raw = raw.rstrip(b'\0')
            frame = json.loads(raw.decode('utf-8'))
            if not isinstance(frame, dict):
                continue
            # Skip notifications (no 'id' field) — we only care about responses
            if 'id' not in frame:
                continue
            return frame

    # -- lifecycle -----------------------------------------------------------

    def pick_and_attach(self, chat_id):
        """One getTargets → the page whose URL contains claude.ai/chat/<chat_id> →
        attachToTarget {flatten:true} on that target only.

        Returns the sessionId, which is then sent on every subsequent message,
        or raises RuntimeError.
        """
        targets_resp = self.call("Target.getTargets", {})
        targets = targets_resp.get("targetInfos", [])
        marker = "claude.ai/chat/" + str(chat_id)
        selected = None
        for info in targets:
            url = str(info.get("url", ""))
            base = url.split("?", 1)[0].split("#", 1)[0]
            if info.get("type") == "page" and marker in base:
                selected = info
                break
        if selected is None:
            raise RuntimeError(f"Page claude.ai/chat/{chat_id} introuvable")
        resp = self.call("Target.attachToTarget",
                         {"targetId": selected.get("targetId"), "flatten": True})
        sid = resp.get("sessionId")
        if isinstance(sid, str):
            self._session_id = sid
        return sid

    def call(self, method, params=None):
        """Send a CDP command and return the result dict."""
        self._id += 1
        msg_id = self._id
        cmd = {"id": msg_id, "method": method, "params": params or {}}
        if self._session_id is not None:
            cmd["sessionId"] = self._session_id
        self._send(cmd)
        resp = self._receive()
        if resp.get("id") != msg_id:
            raise RuntimeError(f"Unexpected response id: expected {msg_id}, got {resp.get('id')}")
        if "error" in resp:
            raise RuntimeError(resp["error"].get("message", "CDP error"))
        return resp.get("result", {})

    def evaluate(self, expression):
        """Runtime.evaluate with returnByValue + awaitPromise; return value."""
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError("CDP Runtime.evaluate exception")
        value = result.get("result") or {}
        if not isinstance(value, dict):
            raise RuntimeError("CDP evaluate result not a dict")
        return value.get("value")

    def insert_text(self, text):
        """Input.insertText on the focused element."""
        self.call("Input.insertText", {"text": text})

    def close(self):
        """Close the reader side; do NOT kill the child here."""
        try:
            self._reader.close()
        except Exception:
            pass
        try:
            self._writer.close()
        except Exception:
            pass

    @property
    def proc(self):
        return self._proc


# ---------------------------------------------------------------------------
# Profile / lock helpers
# ---------------------------------------------------------------------------

def _profile_dir():
    return Path(os.environ.get(
        "CLAUDE_CDP_PROFILE",
        os.path.expanduser("~/.local/share/claude-cdp/chrome")))


def _chrome_bin():
    return os.environ.get(
        "CLAUDE_CDP_CHROME",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _hostname_pid_lock(profile):
    """Return (lock_path, hostname-pid symlink path)."""
    hostname = socket.gethostname()
    state = profile.parent / "cdp-state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state / f"{hostname}-pid"
    return lock_path, lock_path


def sweep_dead_lock(lock_path):
    """If the PID file points to a dead process, unlink it. Return True if swept."""
    if not lock_path.exists():
        return False  # nothing to sweep
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 0)
        return False  # still alive
    except (ValueError, OSError):
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True


def check_profile_busy(lock_path):
    """Return {'ok': False, 'status': 'profile_busy', ...} or None if free."""
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 0)
        mtime = lock_path.stat().st_mtime
        return {'ok': False, 'status': 'profile_busy', 'pid': pid,
                'lock_mtime': mtime}
    except (ValueError, OSError):
        return None


def write_lock(lock_path, pid):
    """Write current PID to the lock file."""
    lock_path.write_text(str(pid))


# ---------------------------------------------------------------------------
# Chrome launcher
# ---------------------------------------------------------------------------

def build_chrome_args(profile):
    """Return the argument list for Chrome (without the binary itself)."""
    return [
        "--remote-debugging-pipe",
        f"--user-data-dir={profile}",
        "--app=https://claude.ai",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def launch_chrome(profile=None):
    """Spawn Chrome in --remote-debugging-pipe mode; return (PipeTransport, proc).

    Chrome reads commands from its fd 3 and writes responses to its fd 4
    (devtools_pipe_handler.cc, chromium main). Parent side:
      - create pipe A (rA/wA: parent writes wA, Chrome reads rA on fd 3) and
        pipe B (rB/wB: Chrome writes wB on fd 4, parent reads rB);
      - save any existing parent fds 3/4 (F_DUPFD above 4, sockets in the
        daemon), dup2 rA→3 and wB→4, then Popen with pass_fds=(3, 4) and
        stdin/stdout/stderr on DEVNULL;
      - RESTORE the parent's original fds 3/4 right after the spawn, never
        closing them for good; a pipe end that replaced an absent fd is closed.
    The transport then reads rB and writes wA through constructor parameters.
    Singleton via PID lock (DÉCISION 6): sweep before spawn, write on success.
    """
    if profile is None:
        profile = _profile_dir()
    lock_path, _ = _hostname_pid_lock(profile)
    sweep_dead_lock(lock_path)

    chrome_bin = _chrome_bin()
    if not Path(chrome_bin).exists():
        raise RuntimeError(f"Binaire Chrome introuvable: {chrome_bin}")

    rA, wA = os.pipe()  # commands: parent writes wA, Chrome reads rA on fd 3
    rB, wB = os.pipe()  # responses: Chrome writes wB on fd 4, parent reads rB

    env = dict(os.environ)
    env.pop('DISPLAY', None)
    env.pop('WAYLAND_DISPLAY', None)

    # Save the parent's fds 3/4 above the pipe range so the dup2 below can
    # never clobber a saved handle.
    saved = []
    for fd in (3, 4):
        try:
            saved.append((fd, fcntl.fcntl(fd, fcntl.F_DUPFD, 5)))
        except OSError:
            saved.append((fd, None))

    def restore():
        for fd, dup in saved:
            if dup is not None:
                try:
                    os.dup2(dup, fd)
                    os.close(dup)
                except OSError:
                    pass
            else:
                try:
                    os.close(fd)
                except OSError:
                    pass

    proc = None
    try:
        os.dup2(rA, 3)  # child fd 3: read end of the command pipe
        os.dup2(wB, 4)  # child fd 4: write end of the response pipe
        # The originals rA/wB now live only at the child's fds 3/4.
        os.close(rA)
        os.close(wB)
        try:
            proc = subprocess.Popen(
                [chrome_bin] + build_chrome_args(profile),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                pass_fds=(3, 4),
                env=env,
            )
        except OSError as exc:
            raise RuntimeError(f"Impossible de lancer Chrome: {exc}") from exc
    finally:
        restore()
        if proc is None:
            for fd in (rB, wA):
                try:
                    os.close(fd)
                except OSError:
                    pass

    if proc.poll() is not None:
        for fd in (rB, wA):
            try:
                os.close(fd)
            except OSError:
                pass
        raise RuntimeError(f"Chrome exited immediately with code {proc.returncode}")

    write_lock(lock_path, proc.pid)
    return PipeTransport(proc, rB, wA), proc


def terminate_chrome(proc, timeout=10.0):
    """SIGTERM → wait bounded → SIGKILL."""
    if proc is None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ret = proc.wait(timeout=0.2)
            return ret
        except subprocess.TimeoutExpired:
            continue
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test helper: bidirectional echo without Chrome
# ---------------------------------------------------------------------------

def test_bidirectional():
    """Round-trip test using cat as child (mirrors Chrome pipe protocol)."""
    rA, wA = os.pipe()  # parent writes to wA → child reads from rA (stdin)
    rB, wB = os.pipe()  # child writes to wB (stdout) → parent reads from rB

    proc = subprocess.Popen(
        ['cat'],
        stdin=rA, stdout=wB, stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    os.close(rA)
    os.close(wB)

    # Send a JSON frame + NUL via raw write
    payload = json.dumps({"hello": "world"}).encode() + b'\0'
    os.write(wA, payload)
    os.close(wA)

    # Read back via raw read (non-blocking loop to avoid blocking on buffered reader)
    raw = bytearray()
    deadline = time.monotonic() + 3
    while b'\0' not in raw and time.monotonic() < deadline:
        try:
            chunk = os.read(rB, 4096)
            if not chunk:
                break
            raw.extend(chunk)
        except BlockingIOError:
            time.sleep(0.05)

    os.close(rB)
    proc.wait(timeout=5)

    assert b'{"hello": "world"}' in raw, f"Expected echo, got {raw!r}"
    return True


if __name__ == '__main__':
    print("Test aller-retour sans Chrome...")
    test_bidirectional()
    print("OK — pipe framing fonctionne.")
