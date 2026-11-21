import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe as adapter


class MemorySocket:
    def __init__(self, frame, chunk_size=4093):
        self.frame = frame
        self.chunk_size = chunk_size
        self.sent = b''
        self.closed = False

    def settimeout(self, timeout):
        pass

    def sendall(self, frame):
        self.sent += frame

    def recv(self, size):
        size = min(size, self.chunk_size)
        chunk, self.frame = self.frame[:size], self.frame[size:]
        return chunk

    def close(self):
        self.closed = True


class HolderByteLimitTests(unittest.TestCase):
    def test_capture_limit_roundtrips_ascii_cjk_emoji_and_escaped_controls(self):
        for character in ('A', '漢', '😀', '\x00'):
            with self.subTest(character=character):
                answer = character * adapter.MAX_CAPTURE_CHARS
                receipt = {'status': 'confirmed', 'response_text': answer,
                           'response_excerpt': answer[:adapter.RESPONSE_EXCERPT_CHARS]}
                frame = (json.dumps(receipt, sort_keys=True, ensure_ascii=False)
                         + '\n').encode('utf-8')
                sock = MemorySocket(frame)
                self.assertEqual(adapter._holder_exchange(
                    sock, {'prompt': 'question', 'request_key': 'bytes'}), receipt)
                self.assertTrue(sock.closed)

    def test_request_uses_actual_json_bytes_and_accepts_escaped_control_limit(self):
        payload = {'prompt': '\x00' * adapter.MAX_PROMPT_CHARS, 'request_key': 'bytes'}
        request = adapter._encode_holder_frame(payload)
        self.assertEqual(len(request), len(
            (json.dumps(payload, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')))
        self.assertLessEqual(len(request), adapter.MAX_REQUEST_BYTES)
        conn = MemorySocket(request)
        with mock.patch.object(adapter, '_holder_dispatch',
                               return_value={'status': 'refused'}) as dispatch:
            adapter._serve_connection(conn, {}, Path('/unused'), Path('/unused'))
        self.assertEqual(dispatch.call_args.args[-1], payload)

    def test_exact_frame_boundaries_in_both_directions(self):
        for limit in (adapter.MAX_REQUEST_BYTES, adapter.MAX_RESPONSE_BYTES):
            for delta in (-1, 0, 1):
                with self.subTest(limit=limit, delta=delta):
                    overhead = len(adapter._encode_holder_frame({'text': ''}))
                    frame = adapter._encode_holder_frame({'text': 'A' * (limit - overhead + delta)})
                    self.assertEqual(len(frame), limit + delta)
                    if delta > 0:
                        with self.assertRaises(ValueError):
                            adapter._read_holder_frame(MemorySocket(frame), limit)
                    else:
                        self.assertEqual(adapter._read_holder_frame(
                            MemorySocket(frame), limit)['text'], 'A' * (limit - overhead + delta))

    def test_oversized_request_is_not_sent_or_dispatched(self):
        sock = MemorySocket(b'{}\n')
        self.assertIsNone(adapter._holder_exchange(
            sock, {'prompt': 'A' * adapter.MAX_REQUEST_BYTES}))
        self.assertEqual(sock.sent, b'')
        with mock.patch.object(adapter, '_holder_dispatch') as dispatch:
            adapter._serve_connection(
                MemorySocket(b' ' * (adapter.MAX_REQUEST_BYTES + 1)),
                {}, Path('/unused'), Path('/unused'))
        dispatch.assert_not_called()
