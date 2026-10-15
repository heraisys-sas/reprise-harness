"""Shared process isolation and bounded JSONL decoding."""
import json
import os
import signal
import subprocess


def codex_environment():
    return {key: value for key, value in os.environ.items()
            if key not in ('OPENAI_API_KEY', 'OPENAI_BASE_URL')}


def terminate(process, grace=0):
    def send(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    try:
        if grace:
            send(signal.SIGTERM)
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
    finally:
        send(signal.SIGKILL)
        process.wait()
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                stream.close()


class JsonLines:
    def __init__(self, limit=1024 * 1024):
        self.limit = limit
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, chunk):
        records = []
        for index, part in enumerate(chunk.split(b'\n')):
            if index:
                if not self.discarding:
                    try:
                        value = json.loads(self.buffer)
                        if isinstance(value, dict):
                            records.append(value)
                    except (ValueError, UnicodeDecodeError):
                        pass
                self.buffer.clear()
                self.discarding = False
            if len(self.buffer) + len(part) > self.limit:
                self.buffer.clear()
                self.discarding = True
            if not self.discarding:
                self.buffer.extend(part)
        return records
