import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import unittest
from unittest.mock import Mock
from chatgpt_mcp import ChatGPTMCP, chatgpt_backends_payload, OUTBOUND
from claude_mcp import outbound_tools, CHATGPT_REMOVED_BACKENDS
from codex_expert_mcp import Bridge, tool_content
from desktop_task_store import TaskStore


def call_tool(facade, name, args):
    response = facade.dispatch({'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
    return json.loads(response['content'][0]['text'])


class ChatgptMcpToolsTests(unittest.TestCase):
    def test_tools_list_v1_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            facade = ChatGPTMCP(tasks, Mock())
            listed = facade.dispatch({'method': 'tools/list'})['tools']
            names = {item['name'] for item in listed}
            self.assertTrue({'get_task', 'submit_result', 'get_result',
                             'reprise_delegate', 'reprise_requests', 'reprise_backends'} <= names)
            # v1 policy: no queue, no bounded wait, no session registration.
            self.assertNotIn('claim_next', names)
            self.assertNotIn('await_task', names)
            self.assertNotIn('register_session', names)
            for item in listed:
                backend = item['inputSchema'].get('properties', {}).get('backend')
                if backend and 'enum' in backend:
                    self.assertNotIn('chatgpt', backend['enum'])
            info = facade.dispatch({'method': 'initialize'})
            self.assertIn('version', info['serverInfo'])
            self.assertIn('loopback', info['instructions'])
            tasks.close()

    def test_chatgpt_loopback_refused_before_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            bridge = Mock()
            facade = ChatGPTMCP(tasks, bridge)
            call = {'method': 'tools/call', 'params': {'name': 'reprise_delegate',
                                                       'arguments': {'backend': 'chatgpt', 'question': 'x'}}}
            with self.assertRaises(ValueError) as caught:
                facade.dispatch(call)
            self.assertIn('ChatGPT loopback is disabled', str(caught.exception))
            bridge.dispatch.assert_not_called()
            tasks.close()

    def test_outbound_delegates_to_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            bridge = Mock()
            bridge.dispatch.return_value = {'existing': 'dispatch'}
            facade = ChatGPTMCP(tasks, bridge)
            call = {'method': 'tools/call', 'params': {'name': 'reprise_delegate',
                                                       'arguments': {'backend': 'codex', 'question': 'Revue'}}}
            self.assertEqual(facade.dispatch(call), {'existing': 'dispatch'})
            bridge.dispatch.assert_called_once_with(call)
            tasks.close()

    def test_backends_payload_filtered_list_and_dict(self):
        listed = {'backends': [{'id': 'codex'}, {'id': 'chatgpt'}, 'chatgpt']}
        self.assertEqual(chatgpt_backends_payload(listed)['backends'], [{'id': 'codex'}])
        mapped = {'backends': {'codex': {'id': 'codex'}, 'chatgpt': {'id': 'chatgpt'}}}
        self.assertEqual(chatgpt_backends_payload(mapped)['backends'], {'codex': {'id': 'codex'}})
        # No backends field: payload untouched.
        self.assertEqual(chatgpt_backends_payload({'status': 'ok'}), {'status': 'ok'})

    def test_backends_call_removes_chatgpt_from_bridge_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            bridge = Mock()
            bridge.dispatch.return_value = tool_content({'status': 'finished',
                                                         'backends': [{'id': 'codex'}, {'id': 'chatgpt'}]})
            facade = ChatGPTMCP(tasks, bridge)
            payload = call_tool(facade, 'reprise_backends', {})
            self.assertEqual(payload['backends'], [{'id': 'codex'}])
            tasks.close()

    def test_unknown_inbox_tool_never_reaches_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            bridge = Mock()
            facade = ChatGPTMCP(tasks, bridge)
            with self.assertRaises(ValueError):
                facade.dispatch({'method': 'tools/call', 'params': {'name': 'register_session',
                                                                    'arguments': {'chat_id': 'c'}}})
            with self.assertRaises(ValueError):
                facade.dispatch({'method': 'tools/call', 'params': {'name': 'claim_next',
                                                                    'arguments': {'claim_key': 'ck'}}})
            bridge.dispatch.assert_not_called()
            tasks.close()


class ChatgptMcpRoundtripTests(unittest.TestCase):
    def test_get_task_submit_result_get_result_close_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            facade = ChatGPTMCP(tasks, Mock())
            tasks.admit('key-156', 'request')
            receipt = call_tool(facade, 'get_task', {'request_key': 'key-156', 'claim_key': 'chatgpt-stable'})
            self.assertFalse(receipt['completed'])
            self.assertIn('attempt_id', receipt)
            call_tool(facade, 'submit_result', {'request_key': 'key-156',
                                                'attempt_id': receipt['attempt_id'], 'answer': 'reponse 156'})
            full = call_tool(facade, 'get_result', {'request_key': 'key-156', 'view': 'full'})
            self.assertEqual(full['answer'], 'reponse 156')
            # Result is immutable: a second read returns the same answer.
            again = call_tool(facade, 'get_result', {'request_key': 'key-156', 'view': 'full'})
            self.assertEqual(again['answer'], 'reponse 156')
            tasks.close()

    def test_exact_arguments_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            facade = ChatGPTMCP(tasks, Mock())
            with self.assertRaises(ValueError):
                call_tool(facade, 'get_task', {'request_key': 'k', 'claim_key': 'c', 'extra': True})
            with self.assertRaises(ValueError):
                call_tool(facade, 'submit_result', {'request_key': 'k'})
            tasks.close()


    def test_transport_budget_accepts_two_million_char_utf8_answer(self):
        # The facade uses CLAUDE_MAX_LINE_BYTES (8 MiB) so a 2M-char multibyte
        # submit_result answer round-trips through one stdio line.
        import io
        import mcp_transport
        with tempfile.TemporaryDirectory() as tmp:
            tasks = TaskStore(tmp, tmp)
            facade = ChatGPTMCP(tasks, Mock())
            tasks.admit('key-utf8', 'request')
            receipt = call_tool(facade, 'get_task', {'request_key': 'key-utf8', 'claim_key': 'chatgpt-utf8'})
            answer = 'é' * 2_000_000
            call_tool(facade, 'submit_result', {'request_key': 'key-utf8',
                                                'attempt_id': receipt['attempt_id'], 'answer': answer})
            message = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'answer': answer}},
                                 ensure_ascii=False).encode('utf-8')
            self.assertGreater(len(message), mcp_transport.CODEX_MAX_LINE_BYTES)
            self.assertLess(len(message), mcp_transport.CLAUDE_MAX_LINE_BYTES)
            parsed = mcp_transport.read_message(io.BytesIO(message), mcp_transport.CLAUDE_MAX_LINE_BYTES)
            self.assertEqual(parsed['result']['answer'], answer)
            tasks.close()


class ChatgptMcpOutboundSchemaTests(unittest.TestCase):
    def test_outbound_schema_helper_removes_only_chatgpt(self):
        tools = outbound_tools(CHATGPT_REMOVED_BACKENDS)
        for item in tools:
            backend = item['inputSchema'].get('properties', {}).get('backend')
            if backend and 'enum' in backend:
                self.assertNotIn('chatgpt', backend['enum'])
                self.assertIn('codex', backend['enum'])

    def test_bridge_init_allows_facade_without_chatgpt_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Bridge(tmp, {}, Path(tmp)/'state', daily_limit=2)
            self.addCleanup(bridge.close)
            self.assertIsNone(bridge.chatgpt_tasks)

    def test_main_wiring_opens_endpoint_and_bridge_chatgpt_tasks(self):
        # Mirrors chatgpt_mcp.main(): Endpoint owns the ledger + roundtrip
        # TaskStore, the Bridge receives the state dir as absolute socket path.
        from chatgpt_quick import Endpoint
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)/'project'
            project.mkdir()
            state = Path(tmp)/'state'
            endpoint = Endpoint(state, project)
            self.addCleanup(endpoint.close)
            bridge = Bridge(str(project), {}, Path(tmp)/'expert', daily_limit=2,
                            desktop_state=state, allowed_tools=OUTBOUND,
                            chatgpt_socket=str(state))
            self.addCleanup(bridge.close)
            self.assertIsNotNone(bridge.chatgpt_tasks)
            facade = ChatGPTMCP(endpoint.tasks, bridge)
            # The wired facade serves the full v1 surface end to end.
            names = {item['name'] for item in facade.dispatch(
                {'method': 'tools/list'})['tools']}
            self.assertTrue({'get_task', 'submit_result', 'get_result'} <= names)
            self.assertFalse(names & {'claim_next', 'await_task', 'register_session'})


class StdioLoopExitTests(unittest.TestCase):
    def test_run_stdio_loop_exits_after_write_failure(self):
        # Historical observable behavior (claude_mcp): a broken stdout (client
        # gone) ends the loop instead of lingering as a zombie server.
        import io
        import mcp_transport

        class Broken:
            closed = mcp_transport.threading.Event()

            def send(self, payload):
                self.closed.set()
                raise OSError('pipe closed')

        class Handler(mcp_transport.StdioHandler):
            def request(self, message, connection):
                connection.send({'id': message['id']})

            def on_error(self, message, ident, exc, connection):
                connection.send({'id': ident, 'error': str(exc)})

        source = io.BytesIO(b'{"jsonrpc": "2.0", "id": 1, "method": "x"}\n'
                            b'{"jsonrpc": "2.0", "id": 2, "method": "x"}\n')
        connection = Broken()
        calls = []
        handler = Handler()
        handler.request = lambda message, conn: calls.append(message['id']) or conn.send({})
        with self.assertRaises(OSError):
            mcp_transport.run_stdio_loop(source, connection, handler)
        self.assertEqual(calls, [1])

    def test_run_stdio_loop_exits_when_already_closed(self):
        import io
        import mcp_transport

        class Closed:
            closed = mcp_transport.threading.Event()

            def send(self, payload):
                raise AssertionError('no write after close')

        connection = Closed()
        connection.closed.set()
        mcp_transport.run_stdio_loop(io.BytesIO(b'{"id": 1}\n'), connection,
                                     mcp_transport.StdioHandler())


if __name__ == '__main__':
    unittest.main()
