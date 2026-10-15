"""Read account metadata through Codex's local stdio app-server; never start a turn."""
import json
import os
import selectors
import signal
import subprocess
import time


def read_limits(executable, timeout=15):
    env = dict(os.environ)
    for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL'):
        env.pop(key, None)
    process = subprocess.Popen([executable, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    total = 0
    def send(value):
        process.stdin.write((json.dumps(value) + '\n').encode())
        process.stdin.flush()
    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'reprise', 'version': '0.1.0'}, 'capabilities': None}})
        while time.monotonic() < deadline:
            for key, _ in selector.select(.2):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    return None
                total += len(chunk)
                if total > 2 * 1024 * 1024:
                    return None
                buffer.extend(chunk)
                while b'\n' in buffer:
                    line, _, rest = buffer.partition(b'\n'); buffer[:] = rest
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if message.get('id') == 1:
                        if 'error' in message:
                            return None
                        send({'method': 'initialized'})
                        send({'id': 2, 'method': 'account/rateLimits/read', 'params': {}})
                    elif message.get('id') == 2:
                        result = message.get('result')
                        return result if isinstance(result, dict) else None
        return None
    finally:
        selector.close()
        process.stdin.close(); process.stdout.close()
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def next_retry(result, now=None):
    now = time.time() if now is None else now
    if not result:
        return None
    if result.get('ordinaryUsageAllowed') is True:
        return now + 120
    buckets = result.get('rateLimitsByLimitId') or {'default': result.get('rateLimits')}
    deadlines = []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        for name in ('primary', 'secondary'):
            window = bucket.get(name) or {}
            reset = window.get('resetsAt')
            if window.get('usedPercent', 0) >= 100 and isinstance(reset, (int, float)) and reset > now:
                deadlines.append(reset)
    return max(deadlines) + 120 if deadlines else None
