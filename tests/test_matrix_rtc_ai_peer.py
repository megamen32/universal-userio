import wave
from pathlib import Path
import importlib.util
import sys

_path = Path(__file__).parent / "manual/matrix_rtc_ai_peer.py"
_spec = importlib.util.spec_from_file_location("matrix_rtc_ai_peer", _path)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
initial_phrase_path, receive_ai_peer = _module.initial_phrase_path, _module.receive_ai_peer


def test_initial_phrase_receives_contextual_reply_and_publishes_tts(tmp_path):
    fixture = initial_phrase_path()
    if not fixture.exists():
        fixture = tmp_path / "caller.wav"
        with wave.open(str(fixture), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000)
            out.writeframes((b"\x01\x00" * 800))
    published = []
    result = __import__("asyncio").run(receive_ai_peer(
        fixture,
        transcribe=lambda pcm: "Скажи, пожалуйста, что ты слышишь?",
        draft=lambda text: f"Я услышал: {text}",
        synthesize=lambda text: b"\x02\x00" * 480,
        publish=published.append,
    ))
    assert result.transcript.startswith("Скажи")
    assert result.response.startswith("Я услышал")
    assert published == [result.published_pcm]
