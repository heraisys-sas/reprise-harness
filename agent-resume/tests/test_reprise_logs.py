import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_logs as rl

MAIN = """2026-10-19 10:58:34 [info] Quick entry submitted { promptLength: 383, hasImage: false, chatId: '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3' }
2026-10-19 10:58:34 [info] IQE: No image provided
2026-10-19 10:58:34 [info] [QuickEntry] Processing quick entry payload { hasText: true, hasImage: false }
2026-10-19 10:58:34 [info] [QuickEntry] Sending to specific chat: 178dcceb-fcd3-4f2d-ac4f-116a8a153bc3
2026-10-19 10:58:34 [info] [QuickEntry] Submitting payload to the open chat
"""

INTERRUPTED = """2026-10-19 11:00:00 [info] Quick entry submitted { promptLength: 50, hasImage: false, chatId: 'aaaaaaa-fcd3-4f2d-ac4f-116a8a153bc3' }
2026-10-19 11:00:00 [info] [QuickEntry] Processing quick entry payload { hasText: true, hasImage: false }
2026-10-19 11:00:01 [warn] [QuickEntry] inactive_account: the active account changed since the prompt was submitted; dropping payload
"""

NO_DISPATCH = """2026-10-19 11:05:00 [info] Quick entry submitted { promptLength: 20, hasImage: false, chatId: 'bbbbbbb-fcd3-4f2d-ac4f-116a8a153bc3' }
2026-10-19 11:05:00 [info] [QuickEntry] Processing quick entry payload { hasText: true, hasImage: false }
"""

MCP = """2026-10-19T08:58:46.825Z [reprise_quick_probe] [info] Message from client: method="tools/call" id=2 params { metadata: undefined }
2026-10-19T08:58:46.825Z [reprise_quick_probe] [info] Message from server: id=2 result { metadata: undefined }
"""


class ParseTests(unittest.TestCase):
    def test_dispatched_submission_not_resumable(self):
        events = rl.parse_quick_entry(MAIN)
        self.assertEqual(events[0]['kind'], 'submitted')
        self.assertEqual(events[0]['chatId'], '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')
        groups = rl._group_submissions(events)
        self.assertEqual(groups[0]['terminal'], 'dispatched')
        # A dispatched submission has no missing/guarded step, so it is not resumable.
        self.assertFalse(any(st['kind'] in ('guard', 'error') for st in groups[0]['steps']))

    def test_guard_marks_interrupted_submission_resumable(self):
        groups = rl._group_submissions(rl.parse_quick_entry(INTERRUPTED))
        self.assertEqual(groups[0]['terminal'], 'guarded')

    def test_missing_dispatch_is_resumable(self):
        groups = rl._group_submissions(rl.parse_quick_entry(NO_DISPATCH))
        self.assertEqual(groups[0]['terminal'], 'submitted')

    def test_interactions_parse_method_and_id(self):
        events = rl.parse_interactions(MCP)
        self.assertEqual(events[0]['method'], 'tools/call')
        self.assertEqual(events[0]['request_id'], '2')
        self.assertEqual(events[0]['server'], 'reprise_quick_probe')


class FunctionalTests(unittest.TestCase):
    def test_resumable_finds_interrupted_only(self):
        # dispatch: not resumable; guard and no-dispatch: resumable
        for text, expect_resumable in ((MAIN, False), (INTERRUPTED, True), (NO_DISPATCH, True)):
            groups = rl._group_submissions(rl.parse_quick_entry(text))
            s = groups[0]
            is_resumable = s['terminal'] in ('guarded', 'error') or (
                s['terminal'] == 'submitted' and not any(
                    st['kind'] == 'routing' and st.get('target', '').startswith('specific') for st in s['steps']))
            self.assertEqual(is_resumable, expect_resumable, text[:40])

    def test_history_reports_note_and_counts(self):
        # history() reads real logs; assert structure only, not fabricated data.
        h = rl.history()
        self.assertIn('submissions', h)
        self.assertIn('interactions', h)
        self.assertIn('timeline', h)
        self.assertIn('not proof', h['note'])


if __name__ == '__main__':
    unittest.main()
