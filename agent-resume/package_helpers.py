"""Build the relocatable helper payload; no runtime or user state is copied."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from package_runtime import metadata


ASSETS = ('REPRISE-AGENTS.template.md', 'CODEX-EXECUTION-CONTRACT.md', 'HANDOFF-CONTRACT.md', 'CLAUDE-CONNECTOR.md', 'VERSION', 'ironclaw.py', 'install-ux-review.py', 'ironclaw_ssh.py', 'ironclaw_tasks.py', 'ironclaw_connection.py', 'IRONCLAW-SSH.md', 'scheduler_service.py', 'harness_integration.py', 'DELEGATION-PERSISTENCE.md', 'QUOTA-KNOWLEDGE.md', 'HARNESS-SUPERVISION-ARCHITECTURE.md', 'HARNESS-INTEGRATION.md', 'CLARIFICATION-CONTRACT.md', 'app_paths.py', 'state_install.py', 'state_setup.py', 'ux_review_setup.py', 'claude_desktop.py', 'claude_desktop_cli.py', 'claude_desktop_quick.py', 'claude_desktop_rpc.py', 'claude_expert.py', 'claude_quota.py', 'claude_mcp.py', 'desktop_task_store.py', 'claude_integration.py', 'codex_expert.py', 'codex_expert_mcp.py', 'codex_rpc.py', 'connection.py', 'dashboard.html', 'dashboard.py', 'delegation_store.py', 'delegation_collect.py', 'desktop_app_probe.py', 'desktop_job_probe.py', 'desktop_mode.py', 'detect.py', 'engine_events.py', 'engines.py', 'harness-events.cjs', 'harness-output-resume.cjs', 'install-expertise.py', 'install-quick-entry.py', 'local_analysis.py', 'owner_route.py', 'package_helpers.py', 'presentation.py', 'private_search.py', 'prompt.example.txt', 'quota.py', 'quota_knowledge.py', 'reprise-delegation.md', 'reprise_health.py', 'reprise_logs.py', 'resume', 'run_status.py', 'runtime.py', 'scheduler.py', 'shared_mode.py', 'task_archive.py', 'ux_review_probe.py', 'vault.py', 'verdict-schema.json')

def package(source, app):
    source, app = Path(source).resolve(), Path(app).resolve()
    resources = app / 'Contents/Resources'
    resources.mkdir(parents=True, exist_ok=True)
    # Only reviewed source asset classes; never copy state, sockets or credentials.
    files = [source / name for name in ASSETS]
    with tempfile.TemporaryDirectory(dir=resources) as temporary:
        payload = Path(temporary) / 'helpers'
        payload.mkdir()
        for item in files:
            shutil.copyfile(item, payload / item.name)
        manifest = {'version': (source / 'VERSION').read_text().strip(),
                    'runtime': metadata(app),
                    'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
        (payload / 'payload-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        destination = resources / 'helpers'
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(payload), destination)
    return destination


if __name__ == '__main__':
    package(sys.argv[1], sys.argv[2])
