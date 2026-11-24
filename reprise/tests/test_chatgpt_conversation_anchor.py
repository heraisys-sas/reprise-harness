# Issue #260 : ancre de conversation ChatGPT (window_continuation).
# Le drain combiné capture l'identifiant de conversation annoncé par les
# frames conversation-created, le porte dans le reçu durable, signale une
# divergence sans refuser, et reste borné/inoffensif sur des frames malformes.
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe as adapter
from tests.test_chatgpt_desktop_pipe import HolderHarness, TestGuardedSend

CONVERSATION_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
OTHER_ID = '11111111-2222-3333-4444-555555555555'


def frame(payload):
    return {'params': {'response': {'payloadData': payload}}}


def created_frame(conversation_id):
    return frame('event: conversation-created\n'
                 'data: {"conversation_id": "' + conversation_id + '"}')


TURN_COMPLETE_FRAME = frame('event: conversation-turn-complete')


class DrainLifecycleTests(unittest.TestCase):
    def test_completed_and_id_extracted_in_one_pass(self):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = [
            created_frame(CONVERSATION_ID), TURN_COMPLETE_FRAME]
        completed, conversation_id, divergent = adapter.drain_lifecycle(transport)
        self.assertTrue(completed)
        self.assertEqual(conversation_id, CONVERSATION_ID)
        self.assertFalse(divergent)
        transport.drain_notifications.assert_called_once_with()

    def test_id_without_created_marker_is_ignored(self):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = [
            frame('event: conversation-turn-complete\n'
                  'data: {"conversation_id": "' + CONVERSATION_ID + '"}')]
        completed, conversation_id, divergent = adapter.drain_lifecycle(transport)
        self.assertTrue(completed)
        self.assertIsNone(conversation_id)

    def test_first_id_wins_later_duplicates_ignored(self):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = [
            created_frame(CONVERSATION_ID), created_frame(OTHER_ID)]
        completed, conversation_id, divergent = adapter.drain_lifecycle(transport)
        self.assertFalse(completed)
        self.assertEqual(conversation_id, CONVERSATION_ID)
        self.assertTrue(divergent)

    def test_extraction_bounded_to_first_64k_of_payload(self):
        padding = 'x' * 70000
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = [
            frame('event: conversation-created\n'
                  'data: {"note": "' + padding + '", '
                  '"conversation_id": "' + CONVERSATION_ID + '"}')]
        completed, conversation_id, divergent = adapter.drain_lifecycle(transport)
        self.assertFalse(completed)
        self.assertIsNone(conversation_id)

    def test_malformed_frames_never_raise(self):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = [
            None, 'event: garbage', 42,
            {}, {'params': None}, {'params': {'response': 'not-a-dict'}},
            {'params': {'response': {'payloadData': 7}}},
            created_frame(CONVERSATION_ID), TURN_COMPLETE_FRAME]
        completed, conversation_id, divergent = adapter.drain_lifecycle(transport)
        self.assertTrue(completed)
        self.assertEqual(conversation_id, CONVERSATION_ID)

    def test_drain_turn_completion_delegates_completed_flag(self):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = [
            created_frame(CONVERSATION_ID), TURN_COMPLETE_FRAME]
        self.assertTrue(adapter.drain_turn_completion(transport))
        empty = mock.MagicMock()
        empty.drain_notifications.return_value = []
        self.assertFalse(adapter.drain_turn_completion(empty))


class ConversationIdReceiptTests(unittest.TestCase):
    """End-to-end : le reçu durable porte l'ancre de conversation."""

    def setUp(self):
        self.holder = TestGuardedSend()
        self.holder.setUp()
        self.addCleanup(self.holder._folder.cleanup)

    def invoke(self, sse, request):
        transport = self.holder.fake(sse=sse)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key=request)
        return transport, receipt

    def test_conversation_id_lands_in_confirmed_receipt(self):
        transport, receipt = self.invoke(
            [created_frame(CONVERSATION_ID), TURN_COMPLETE_FRAME],
            'req-260-id')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertEqual(receipt['completion'], 'turn-complete')
        self.assertEqual(receipt['conversation_id'], CONVERSATION_ID)
        self.assertNotIn('conversation_id_divergent', receipt)
        self.assertEqual(receipt['dispatch_action'], 'enter')

    def test_divergent_id_reported_without_refusing_dispatch(self):
        transport, receipt = self.invoke(
            [created_frame(CONVERSATION_ID), created_frame(OTHER_ID),
             TURN_COMPLETE_FRAME],
            'req-260-divergent')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertEqual(receipt['conversation_id'], CONVERSATION_ID)
        self.assertTrue(receipt['conversation_id_divergent'])

    def test_absent_conversation_id_keeps_window_continuation_receipt(self):
        # Session réelle sans frame conversation-created : le reçu reste
        # exploitable (« continuation de la fenêtre courante »), id absent.
        transport, receipt = self.invoke([TURN_COMPLETE_FRAME], 'req-260-no-id')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertIsNone(receipt['conversation_id'])
        self.assertNotIn('conversation_id_divergent', receipt)

    def test_receipt_without_id_still_meets_turn_complete_contract(self):
        # L'ancre manquante ne dégrade pas la preuve de complétude : le
        # marqueur WS reste le critère de fin de tour.
        transport, receipt = self.invoke([TURN_COMPLETE_FRAME], 'req-260-still')
        self.assertTrue(receipt['turn_completed'])
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['conversation_id'], None)

    def test_malformed_lifecycle_frames_never_block_dispatch(self):
        garbage = [None, 'frame', {'params': {}},
                   frame('event: conversation-created\ndata: {broken json'),
                   TURN_COMPLETE_FRAME]
        transport, receipt = self.invoke(garbage, 'req-260-garbage')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['turn_completed'])

    def test_durable_receipt_replay_keeps_conversation_anchor(self):
        # Au-moins-une-fois : le replay d'un reçu stocké rend l'ancre
        # persistée, sans nouveau dispatch.
        transport, receipt = self.invoke(
            [created_frame(CONVERSATION_ID), TURN_COMPLETE_FRAME],
            'req-260-replay')
        self.assertEqual(receipt['conversation_id'], CONVERSATION_ID)
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            replayed = adapter.send('synthetic request', self.holder.binary,
                                    self.holder.root, 'dedicated',
                                    request_key='req-260-replay')
        launch.assert_not_called()
        self.assertTrue(replayed['replayed'])
        self.assertEqual(replayed['conversation_id'], CONVERSATION_ID)


if __name__ == '__main__':
    unittest.main()
