"""Bounded local Apple Foundation Models expertise; no tools or fallback."""
import os
from pathlib import Path

from claude_expert import child_environment, context, run

PROGRAM = '/usr/bin/fm'
MAX_PROMPT_BYTES = 110_000
TIMEOUT_SECONDS = 120


def configured():
    """Executable presence is not proof of a usable model or successful inference."""
    return os.path.isfile(PROGRAM) and os.access(PROGRAM, os.X_OK)


def review(root, question, *, paths=None):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not isinstance(question, str) or not question.strip() or len(question) > 12000:
        raise ValueError('Project directory and bounded question required')
    base = {'backend': 'foundation-models', 'requested_model': 'system',
            'on_device': True, 'resumable': False, 'automatic_fallback': False}
    try:
        prompt = question + context(root, paths)
        if len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES:
            raise ValueError('Prompt and explicit context exceed byte limit')
    except (ValueError, OSError, UnicodeError):
        # This block precedes process creation; do not expose paths or input data.
        return base | {'status': 'apple_input_invalid', 'process_started': False,
                       'reason': 'Explicit input unavailable, invalid or too large; no process started.'}
    if not configured():
        return base | {'status': 'engine_unavailable', 'reason': 'Apple fm executable unavailable; macOS 27 or later required.'}
    try:
        code, output, _ = run([PROGRAM, 'respond', '--model', 'system', '--no-stream'],
                              root, child_environment(), prompt, TIMEOUT_SECONDS)
    except TimeoutError:
        return base | {'status': 'apple_process_failed', 'process_reaped': True,
                       'inference_outcome': 'unknown', 'reason': 'Inference deadline reached and fm exited; no automatic retry.'}
    except (OSError, OverflowError, UnicodeError):
        return base | {'status': 'needs_review', 'reason': 'Apple process failed or returned invalid output; no automatic retry.'}
    if code or not output.strip():
        return base | {'status': 'apple_process_failed', 'process_reaped': True,
                       'inference_outcome': 'unknown', 'reason': 'Apple process exited with an error or an empty response. Check model availability and context size; reduce the input if needed. No automatic retry.'}
    return base | {'status': 'finished', 'response': output.strip()}
