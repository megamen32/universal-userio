"""Offline MatrixRTC AI-peer harness.

The real room/client is injected by a caller; this module never reads
credentials or accepts them on argv. Tests use an in-memory room and WAV.
"""
from __future__ import annotations

import asyncio
import audioop
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class PeerReply:
    transcript: str
    response: str
    published_pcm: bytes


async def receive_ai_peer(
    wav_path: str | Path,
    *,
    transcribe: Callable[[bytes], str],
    draft: Callable[[str], str],
    synthesize: Callable[[str], bytes],
    publish: Callable[[bytes], object],
) -> PeerReply:
    """Receive one prerecorded caller turn and publish one spoken reply.

    The seam matches the production order: incoming media is normalized to
    48kHz mono, STT gets the complete turn, the draft sees the transcript, and
    TTS output is published as one RTC PCM payload.
    """
    with wave.open(str(wav_path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("fixture must be mono PCM16")
        pcm = source.readframes(source.getnframes())
        rate = source.getframerate()
    if rate != 48000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 48000, None)
    transcript = str(await asyncio.to_thread(transcribe, pcm)).strip()
    if not transcript:
        raise ValueError("caller speech was not recognized")
    response = str(await asyncio.to_thread(draft, transcript)).strip()
    if not response:
        raise ValueError("AI peer produced an empty response")
    spoken = bytes(await asyncio.to_thread(synthesize, response))
    if not spoken:
        raise ValueError("TTS produced empty PCM")
    result = publish(spoken)
    if asyncio.iscoroutine(result):
        await result
    return PeerReply(transcript, response, spoken)


def initial_phrase_path() -> Path:
    return Path(__file__).parents[2] / ".tmp/lhc-voice-20261021/caller_phrase_initial.wav"
