"""Exercise the installed Harness with a loopback-only scripted LLM provider."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DSH = Path(os.environ.get('DSH_CLI', '/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh'))
requests = []
errors = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = body['messages']
        tools = [x['function']['name'] for x in body.get('tools', [])]
        child = body['model'] == 'routing-executor'
        finished = any(m.get('role') == 'tool' for m in messages)
        title = not tools and not child and not finished
        if not title: requests.append(body['model'])
        if title:
            delta = {'role': 'assistant', 'content': 'Local routing smoke'}
            reason = 'stop'
        elif not child and not finished:
            if 'subagent' not in tools:
                errors.append('subagent_model missing from actual Harness tools: ' + ','.join(tools))
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'routing-call-1', 'type': 'function', 'function': {'name': 'subagent', 'arguments': json.dumps({'description': 'Assessment', 'prompt': 'Explain the tradeoffs between alternative algorithms.', 'run_in_background': False})}}]}
            reason = 'tool_calls'
        else:
            delta = {'role': 'assistant', 'content': 'CHILD_OK' if child else 'SMOKE_OK'}
            reason = 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for part, finish in [(delta, None), ({}, reason)]:
            event = {'id': 'routing-test', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model'], 'choices': [{'index': 0, 'delta': part, 'finish_reason': finish}]}
            self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.write(('data: '+json.dumps({'id': 'routing-test', 'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110, 'prompt_tokens_details': {'cached_tokens': 40}}})+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='reprise-harness-smoke-') as directory:
        env = {**os.environ, 'DSH_HOME': directory, 'ROUTING_MOCK_KEY': 'local-test-only'}
        subprocess.run([str(DSH), '--profile', 'smoke', '--from-default-profile', 'headless', '--dump-config'], env=env, check=True, capture_output=True)
        subprocess.run([str(DSH), 'plugin', '--profile', 'smoke', 'add', 'file:' + str(ROOT / 'plugin'), '--ignore-scripts', '--offline'], env=env, check=True, capture_output=True)
        patch = f'''- id: agent-default-model
  config:
    provider: routing-mock
    model: routing-original
- id: llm-pi-ai
  config:
    providers:
      routing-mock:
        api: openai-completions
        baseURL: http://127.0.0.1:{server.server_port}/v1
        apiKeyEnv: ROUTING_MOCK_KEY
        models:
          - id: routing-original
          - id: routing-planner
          - id: routing-executor
        retryPolicy:
          mode: normal
          maxRetries: 0
- id: economy-role-router
  config:
    enabled: true
    planner:
      provider: routing-mock
      model: routing-planner
      reasoningEffort: inherit
    executor:
      provider: routing-mock
      model: routing-executor
      reasoningEffort: inherit
'''
        (Path(directory) / 'profiles/smoke/cordis.patch.yml').write_text(patch)
        run = subprocess.run([str(DSH), '--profile', 'smoke', '--json', 'ROUTING_SMOKE_ROOT: delegate the supplied task.'], env=env, capture_output=True, text=True, timeout=30, cwd=directory)
        assert run.returncode == 0 and 'SMOKE_OK' in run.stdout, (run.returncode, run.stdout[-3000:], run.stderr[-1500:])
        assert not errors, (errors, requests, run.stdout[-2500:])
        assert requests == ['routing-planner', 'routing-executor', 'routing-planner'], requests
        assert 'CHILD_OK' in run.stdout, run.stdout
        sys.path.insert(0, str(ROOT.parent / 'harness-tools'))
        from harness_usage import cli_observations
        from usage_report import report
        observed = report(cli_observations([json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')], 'smoke'))
        assert len(observed['requests']) == 2, observed
        assert observed['totals']['completion_tokens'] == 20, observed
        assert observed['totals']['billed_cost'] is None, observed
        print(json.dumps({'passed': True, 'observed_root_steps': len(observed['requests']), 'observed_output_tokens': observed['totals']['completion_tokens'], 'models': requests, 'external_model_requests': 0}))
finally:
    server.shutdown()
