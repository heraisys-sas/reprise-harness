#!/usr/bin/env python3
"""Legacy development installer. Managed bundle installations use harness_integration.py."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sys

START = '# BEGIN REPRISE EXPERTISE'
END = '# END REPRISE EXPERTISE'


def install(home, project, profile='desktop'):
    if not profile or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in profile):
        raise ValueError('Invalid profile name')
    project = Path(project).resolve(strict=True)
    if not project.is_dir():
        raise ValueError('Project directory required')
    home, source = Path(home), Path(__file__).resolve().parent
    target = home/'profiles'/profile/'cordis.patch.yml'
    original = target.read_text()
    if original.count(START) != original.count(END) or original.count(START) > 1:
        raise ValueError('Ambiguous existing bridge block')
    if START in original:
        first, remainder = original.split(START, 1)
        _, last = remainder.split(END, 1)
        base = first + last
    else:
        if 'reprise-codex-expert' in original or 'serverName: reprise_codex' in original:
            raise ValueError('Existing unmanaged bridge requires inspection')
        base = original
    if '\n'.join(line for line in base.splitlines() if line.strip() and not line.lstrip().startswith('#')).strip() == '[]':
        base = '\n'.join(line for line in base.splitlines() if line.lstrip().startswith('#'))
    args = [str(source/'codex_expert_mcp.py'), '--project-root', str(project), '--state', str(source/'state')]
    block = '\n'.join([START, '- insert:', '    - id: reprise-codex-expert',
        "      name: '@deepseek-ai/dsh-mcp-client'", '      config:', '        transport: stdio',
        '        serverName: reprise_codex', '        command: '+json.dumps(sys.executable),
        '        args: '+json.dumps(args, ensure_ascii=False), '        toolCallTimeoutMs: 330000',
        '        failOnStartupError: false', '        reconnect:', '          enabled: false', END, ''])
    updated = base.rstrip()+'\n'+block
    skill = home/'skills/reprise-delegation/SKILL.md'
    files = [(target, updated), (skill, (source/'reprise-delegation.md').read_text())]
    files.append((skill.parent / 'REPRISE-AGENTS.template.md', (source / 'REPRISE-AGENTS.template.md').read_text()))
    files.append((skill.parent / 'HANDOFF-CONTRACT.md', (source / 'HANDOFF-CONTRACT.md').read_text()))
    if all(path.exists() and path.read_text() == text for path, text in files):
        return {'changed': False, 'project_root': str(project)}
    backup = home/'backups'/('reprise-expertise-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    for path, text in files:
        if path.exists():
            saved = backup/path.relative_to(home)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name+'.reprise-tmp')
        temporary.write_text(text)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    return {'changed': True, 'project_root': str(project), 'profile': profile, 'backup': str(backup)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--home', default=str(Path.home()/'.dsh'))
    parser.add_argument('--profile', default='desktop')
    args = parser.parse_args()
    print('Legacy development installer: use the Reprise Integrations screen for managed bundle installation.', file=sys.stderr)
    print(json.dumps(install(args.home, args.project_root, args.profile), ensure_ascii=False))
