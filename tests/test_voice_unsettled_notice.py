"""An uncertain native SDK turn must leave an audible status, never a replay."""
import asyncio
from unittest.mock import patch

import test_voice_answer_first as fixtures

voice_env = fixtures.voice_env


def test_unsettled_turn_speaks_unknown_result_and_stops_queued_work(voice_env):
    bridge = fixtures.bridge
    real_stream = bridge.stream_voice_turn
    admitted = []

    async def uncertain(writer, broker, call_id, text, brain, speech, hung_up, stop, **kwargs):
        if text and not kwargs.get('fixed_answer'):
            admitted.append(text)
            raise bridge.NativeTurnUnsettled('Secretary voice outcome is unknown; inspect receipt')
        return await real_stream(writer, broker, call_id, text, brain, speech, hung_up, stop, **kwargs)

    with patch.object(bridge, 'stream_voice_turn', side_effect=uncertain):
        sent, events = asyncio.run(fixtures.run_prepared_call(
            lambda pcm: 'Выполни моё поручение.', [fixtures.VOICE * 20, fixtures.VOICE * 21, None]))
    spoken = fixtures.agent_turns(events)
    assert any('подтвержден' in text.lower() and 'не повтор' in text.lower() for text in spoken), spoken
    assert len(admitted) == 1
    assert len(sent) >= 2  # The actual audio sender receives greeting and fixed notice.
    assert not any('не выполнено' in text.lower() for text in spoken)
