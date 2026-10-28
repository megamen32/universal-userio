import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('fixture_receipts', Path(__file__).parent/'manual/matrix_fixture_receipts.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FixtureReceiptTests(unittest.TestCase):
    def test_split_utterance_uses_actual_next_turn(self):
        lines = [
            '2026-10-28T18:26:12Z [agentcall] call=qa heard: interrupted phrase',
            '2026-10-28T18:26:17Z [agentcall] voice_turn call=qa turn=3 mode=secretary elapsed_ms=5',
            '2026-10-28T18:26:21.483487715Z [agentcall] call=qa heard: switch to incidents',
            '2026-10-28T18:26:28.129499810Z [agentcall] voice_turn call=qa turn=4 mode=incidents elapsed_ms=7',
        ]
        boundary = __import__('datetime').datetime.fromisoformat('2026-10-28T18:26:20+00:00').timestamp()
        result = m.completed_native_turn(lines, 'qa', boundary)
        self.assertEqual((result['turn_id'], result['mode']), (4, 'incidents'))
        self.assertIsNone(m.completed_native_turn(lines[:-1], 'qa', boundary))

    def test_foreign_and_unfinished_turn_cannot_release_pcm(self):
        lines = [
            '2026-10-28T18:26:21.483487715Z [agentcall] call=qa heard: actual question',
            '2026-10-28T18:26:28.129499810Z [agentcall] voice_turn call=other turn=4 mode=incidents elapsed_ms=7',
            '2026-10-28T18:26:28.129499810Z [agentcall] call=qa native turn failed; no replay',
        ]
        self.assertIsNone(m.completed_native_turn(lines, 'qa', 0))


if __name__ == '__main__':
    unittest.main()
