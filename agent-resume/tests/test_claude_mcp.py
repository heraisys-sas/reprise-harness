import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import unittest
from unittest.mock import Mock
from claude_mcp import ClaudeMCP
from desktop_task_store import TaskStore
from codex_expert_mcp import tool_content


class ClaudeMCPTests(unittest.TestCase):
    def test_composed_tools_and_blocked_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp); bridge = Mock()
            facade = ClaudeMCP(tasks, bridge)
            listed = facade.dispatch({'method':'tools/list'})['tools']
            self.assertTrue({'get_task','submit_result','get_result','reprise_delegate','reprise_followup','reprise_requests'} <= {t['name'] for t in listed})
            for item in listed:
                backend = item['inputSchema'].get('properties', {}).get('backend')
                if backend: self.assertNotIn('claude-desktop', backend['enum'])
            call = {'method':'tools/call','params':{'name':'reprise_delegate','arguments':{'backend':'claude-desktop'}}}
            with self.assertRaises(ValueError): facade.dispatch(call)
            bridge.dispatch.assert_not_called()
            call['params']['arguments']['backend'] = 'codex'
            bridge.dispatch.return_value = {'existing':'dispatch'}
            self.assertEqual(facade.dispatch(call), {'existing':'dispatch'})
            bridge.dispatch.assert_called_once_with(call)
            bridge.dispatch.return_value = tool_content({'status':'finished','backends':[{'id':'codex'},{'id':'claude-desktop'}]})
            payload = facade.dispatch({'method':'tools/call','params':{'name':'reprise_backends'}})
            self.assertEqual(json.loads(payload['content'][0]['text'])['backends'], [{'id':'codex'}])
            self.assertIn('version', facade.dispatch({'method':'initialize'})['serverInfo'])
            tasks.close()

    def test_incoming_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp); facade = ClaudeMCP(tasks, Mock())
            tasks.admit('key', 'request')
            def call(name,args):
                response = facade.dispatch({'method':'tools/call','params':{'name':name,'arguments':args}})
                return json.loads(response['content'][0]['text'])
            receipt = call('get_task', {'request_key':'key','claim_key':'stable'})
            self.assertFalse(receipt['completed'])
            call('submit_result', {'request_key':'key','attempt_id':receipt['attempt_id'],'answer':'done'})
            self.assertEqual(call('get_result', {'request_key':'key','view':'full'})['answer'], 'done')
            with self.assertRaises(ValueError): call('get_task', {'request_key':'key','claim_key':'stable','extra':True})
            tasks.close()

    def test_long_unicode_result_pages_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp); facade = ClaudeMCP(tasks, Mock())
            answer = 'é😀\\"\n' * 12000
            tasks.admit('long', 'request')
            attempt = tasks.claim('long', 'stable')['attempt_id']
            tasks.submit('long', attempt, answer)
            def call(args):
                content = facade.dispatch({'id':'read','method':'tools/call','params':{'name':'get_result','arguments':args}})
                self.assertLessEqual(len(json.dumps({'jsonrpc':'2.0','id':'read','result':content}, ensure_ascii=False)), 7000)
                return json.loads(content['content'][0]['text'])
            manifest = call({'request_key':'long'})
            self.assertNotIn('answer', manifest)
            chunks = []; offset = 0
            while True:
                page = call({'request_key':'long','view':'page','offset':offset,'result_sha256':manifest['result_sha256']})
                chunks.append(page['text'])
                if not page['has_more']: break
                self.assertGreater(page['next_offset'], offset)
                offset = page['next_offset']
            combined = ''.join(chunks)
            self.assertEqual(len(combined), manifest['total_length'])
            self.assertEqual(json.loads(combined)['answer'], answer)
            import hashlib
            self.assertEqual(hashlib.sha256(combined.encode()).hexdigest(), manifest['result_sha256'])
            self.assertFalse(tasks.result('long')['acknowledged'])
            self.assertEqual(call({'request_key':'long','view':'page','result_sha256':'0'*64})['status'], 'result_changed')
            with self.assertRaises(ValueError): call({'request_key':'long','view':'page','offset':1})
            with self.assertRaises(ValueError): call({'request_key':'long','view':'page','acknowledge':True})
            tasks.close()
