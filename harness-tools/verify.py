#!/usr/bin/env python3
"""Run explicitly configured project checks and return bounded JSON evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time

LIMIT = 2 * 1024 * 1024

def redact(text):
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{15,})', '[REDACTED]', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*bearer\s+)\S+', r'\1[REDACTED]', text)
    return re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', text)

def validate(config):
    checks = config.get('checks', [])
    if not isinstance(checks, list) or not 1 <= len(checks) <= 20:
        raise ValueError('Provide 1–20 checks')
    seen = set()
    for check in checks:
        name, argv, timeout = check.get('name'), check.get('argv'), check.get('timeout', 120)
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or name in seen:
            raise ValueError('Unique simple check names required')
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or '\0' in x for x in argv):
            raise ValueError('argv must be a nonempty string array')
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
            raise ValueError('timeout must be 1–600 seconds')
        seen.add(name)
    return checks

def execute(check, root, logs):
    argv = check['argv']
    if shutil.which(argv[0]) is None:
        return {'name': check['name'], 'status': 'unavailable', 'reason': 'executable-not-found'}
    start = time.monotonic()
    process = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    dropped = 0
    timed_out = False
    try:
        while selector.get_map() or process.poll() is None:
            if time.monotonic() - start > check.get('timeout', 120):
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            for key, _ in selector.select(.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                remaining = max(0, LIMIT-len(output))
                output.extend(chunk[:remaining])
                dropped += max(0, len(chunk)-remaining)
        code = process.wait()
    finally:
        selector.close()
        process.stdout.close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    content = redact(output.decode('utf-8', errors='replace'))
    log = logs / (check['name'] + '.log')
    with open(log, 'w', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write(content)
    lines = [line for line in content.splitlines() if re.search(r'error|fail|warning|:\d+:', line, re.I)]
    summary = '\n'.join(lines[:5])[:1200] if lines else content[-600:]
    return {'name': check['name'], 'status': 'passed' if code == 0 and not timed_out else 'failed',
            'exit_code': code, 'timeout': timed_out, 'seconds': round(time.monotonic()-start, 3),
            'diagnostic': summary, 'log': str(log), 'discarded_bytes': dropped}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--root', default='.')
    parser.add_argument('--run', action='store_true', help='Execute checks; otherwise show the plan only')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        checks = validate(json.loads(Path(args.config).read_text()))
        if not args.run:
            print(json.dumps({'status': 'not-run', 'root': str(root), 'checks': checks}))
            return 0
        logs = Path(tempfile.mkdtemp(prefix='harness-checks-'))
        results = [execute(check, root, logs) for check in checks]
        print(json.dumps({'checks': results, 'note': 'Configured commands only; not a security certification. Logs are bounded and redaction is best effort.'}, ensure_ascii=False))
        return 0 if all(x['status'] == 'passed' for x in results) else 1
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'unavailable', 'error': redact(str(error))[:400]}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
