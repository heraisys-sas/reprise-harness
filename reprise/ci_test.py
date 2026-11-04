"""Run deterministic unittest fixtures with a disposable HOME, never user state."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='reprise-test-home-') as home:
        env = dict(os.environ, HOME=home, PYTHONDONTWRITEBYTECODE='1')
        env.pop('AGENT_RESUME_STATE', None)
        return subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s',
                               str(Path(__file__).parent / 'tests'), '-q'], env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
