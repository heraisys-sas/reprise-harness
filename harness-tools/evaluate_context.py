"""Development retrieval checks on repository files, no external requests."""
import json
from pathlib import Path
from context_pack import prepare

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('process timeout kill', 'harness-tools/verify.py', 'SIGKILL'),
    ('cache chiffré index recherche', 'agent-resume/private_search.py', 'encrypted_index'),
    ('preuve evidence analyse locale', 'agent-resume/local_analysis.py', 'Preuve absente'),
    ('route agent lifetime planner executor', 'role-routing/plugin/src/index.ts', 'chosen'),
    ('quota usage_limit_exceeded', 'agent-resume/quota.py', 'usage'),
    ('scheduler reprise permission', 'agent-resume/scheduler.py', 'ordinaryUsageAllowed'),
    ('Rust portage contrats tests différentiels', 'deepseek-harness-config/AGENTS.md', 'Rust'),
    ('Python commandes argv shell', 'harness-tools/verify.py', 'argv'),
]


def evaluate(semantic=False):
    sources = [(name, (ROOT/name).read_text()) for name in sorted({c[1] for c in CASES})]
    instructions = [('AGENTS.md', (ROOT/'deepseek-harness-config/AGENTS.md').read_text())]
    rows = []
    for query, expected, evidence in CASES:
        pack = prepare(query, instructions, sources, 6000, semantic)
        rows.append({'query': query, 'retrieved': any(b['source'] == expected and evidence in b['text'] for b in pack['excerpts']),
                     'source_characters': pack['source_characters'], 'excerpt_characters': pack['excerpt_characters'],
                     'seconds': pack['seconds'], 'semantic_available': pack['semantic_available']})
    return {'cases': rows, 'passed': sum(r['retrieved'] for r in rows), 'total': len(rows),
            'scope': 'Eight FR/EN repository retrieval checks, not end-to-end task competence or paid token savings.'}


if __name__ == '__main__':
    import sys
    print(json.dumps(evaluate('--semantic' in sys.argv), ensure_ascii=False, indent=2))
