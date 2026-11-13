"""Build the relocatable helper payload; no runtime or user state is copied."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from package_runtime import metadata
from yaml_contract import YAML_FILES, yaml_hashes

ASSETS = ('apple_expert.py', 'apple_pilot.py', 'apple_pilot_service.py', 'apple_pilot_transport.py', 'orchestrator_memory.py', 'orchestrator_memory_service.py', 'project_registry.py', 'project_commands.py', 'claude_session_store.py', 'claude_session_observer.py', 'websocket_transport.py', 'cdp_targets.py', 'cdp_targets.json', 'claude_desktop_apply.py', 'claude_quota_log.py', 'claude_quota_banner.py', 'claude_cdp_pipe.py', 'managed_update.py', 'VERSION', 'ironclaw.py', 'install-ux-review.py', 'ironclaw_ssh.py', 'ironclaw_tasks.py', 'ironclaw_connection.py', 'scheduler_service.py', 'harness_integration.py', 'asi1-orchestrator-effort.cjs', 'app_paths.py', 'state_install.py', 'state_setup.py', 'ux_review_setup.py', 'claude_desktop.py', 'claude_desktop_cli.py', 'claude_desktop_quick.py', 'claude_desktop_rpc.py', 'claude_inbox.py', 'claude_expert.py', 'claude_quota.py', 'claude_mcp.py', 'desktop_task_store.py', 'claude_integration.py', 'codex_expert.py', 'codex_expert_mcp.py', 'codex_rpc.py', 'connection.py', 'routing_events.py', 'external_rankings.py', 'openrouter_adapter.py', 'artificial_analysis_adapter.py', 'dashboard.html', 'dashboard.py', 'delegation_store.py', 'delegation_collect.py', 'delegation_reconcile.py', 'desktop_app_probe.py', 'desktop_job_probe.py', 'desktop_mode.py', 'detect.py', 'engine_events.py', 'engines.py', 'harness-events.cjs', 'harness-output-resume.cjs', 'install-expertise.py', 'install-quick-entry.py', 'local_analysis.py', 'owner_route.py', 'package_helpers.py', 'presentation.py', 'private_search.py', 'prompt.example.txt', 'quota.py', 'quota_knowledge.py', 'reprise_health.py', 'reprise_logs.py', 'reprise', 'run_status.py', 'runtime.py', 'scheduler.py', 'shared_mode.py', 'task_archive.py', 'ux_review_probe.py', 'vault.py', 'verdict-schema.json')

ASSETS += ('yaml_contract.py', 'codex_config_update.py',)
ASSETS += ('mcp_transport.py', 'chatgpt_quick.py', 'chatgpt_mcp.py',)  # Issue #156: shared MCP transport + ChatGPT bridge.
ASSETS += ('memory_admin.py',)  # Memory administration packaging; independent of local execution.
ASSETS += ('memory_integration.py', 'reprise_memory_adapter.py', 'reprise_memory_mcp.py', 'coordinator_jobs.py', 'coordinator_local.py', 'coordinator_candidate.py', 'coordinator_recall.py')
# Documentation durable embarquée dans le payload ; résolue depuis la racine du dépôt.
DOCS = (
    'docs/integrations/claude/CLAUDE-DESKTOP-APPLY.md',
    'docs/runbooks/MANAGED-UPDATE.md',
    'docs/runbooks/SCHEDULER-SERVICE.md',
    'docs/templates/REPRISE-AGENTS.template.md',
    'docs/contracts/CODEX-EXECUTION-CONTRACT.md',
    'docs/templates/ASI1-ORCHESTRATOR.template.md',
    'docs/contracts/ASI1-ORCHESTRATION-CONTRACT.md',
    'docs/contracts/HANDOFF-CONTRACT.md',
    'docs/contracts/HARNESS-ROLES-CONTRACT.md',
    'docs/integrations/claude/CLAUDE-CONNECTOR.md',
    'docs/integrations/claude/CLAUDE-INTEGRATION.md',
    'docs/integrations/ironclaw/IRONCLAW-SSH.md',
    'docs/contracts/DELEGATION-PERSISTENCE.md',
    'docs/contracts/QUOTA-KNOWLEDGE.md',
    'docs/contracts/HARNESS-SUPERVISION-ARCHITECTURE.md',
    'docs/integrations/harness/HARNESS-INTEGRATION.md',
    'docs/contracts/CLARIFICATION-CONTRACT.md',
    'docs/integrations/harness/reprise-delegation.md',
    'docs/contracts/ORCHESTRATOR-MEMORY-CONTRACT.md',
    'docs/contracts/APPLE-PILOT-CONTRACT.md',
    'docs/runbooks/MEMORY-ADMIN.md',
)

def doc_asset(source, rel):
    """Documentation embarquée depuis un stage, un export partagé ou la racine du dépôt."""
    staged = source / rel
    if staged.is_file():
        return staged
    flat = source / rel.rsplit('/', 1)[-1]
    return flat if flat.is_file() else source.parent / rel


def package(source, app):
    source, app = Path(source).resolve(), Path(app).resolve()
    resources = app / 'Contents/Resources'
    resources.mkdir(parents=True, exist_ok=True)
    # Only reviewed source asset classes; never copy state, sockets or credentials.
    files = [source / name for name in ASSETS] + [doc_asset(source, name) for name in DOCS]
    with tempfile.TemporaryDirectory(dir=resources) as temporary:
        payload = Path(temporary) / 'helpers'
        payload.mkdir()
        for item in files:
            shutil.copyfile(item, payload / item.name)
        vendor = source / 'vendor/yaml'
        yaml_source_hashes = yaml_hashes(source)
        if not yaml_source_hashes:
            raise ValueError('Répertoire YAML requis absent.')
        if vendor.parent.is_symlink() or vendor.is_symlink():
            raise ValueError('Répertoire YAML lié : packaging refusé.')
        if vendor.is_dir():
            target = payload / 'vendor/yaml'
            target.mkdir(parents=True)
            for name in YAML_FILES:
                item = vendor / name
                if item.is_symlink() or not item.is_file():
                    raise ValueError(f'Fichier YAML requis absent ou lien symbolique : {name}')
                shutil.copyfile(item, target / name)
        if yaml_hashes(payload, exact=True) != yaml_source_hashes:
            raise ValueError('Payload YAML modifié pendant la copie.')
        manifest = {'version': (source / 'VERSION').read_text().strip(),
                    'runtime': metadata(app),
                    'vendor_yaml': yaml_source_hashes,
                    'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
        (payload / 'payload-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        destination = resources / 'helpers'
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(payload), destination)
    return destination


if __name__ == '__main__':
    package(sys.argv[1], sys.argv[2])
