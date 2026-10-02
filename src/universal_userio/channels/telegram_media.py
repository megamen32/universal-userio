"""Describe native Telegram media at the UserIO provider boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MediaDescriptor:
    """Media type and cheap pre-download limits for one incoming attachment."""
    kind: str
    mime_type: str
    size_bytes: int | None = None
    duration_seconds: float | None = None
    emoji: str | None = None


def describe_media(message: Any) -> MediaDescriptor | None:
    """Recognize photos, voice, ordinary videos and video notes without downloading."""
    document = getattr(message, 'document', None)
    file = getattr(message, 'file', None)
    mime = getattr(document, 'mime_type', None) or getattr(file, 'mime_type', None) or ''
    emoji = None
    if getattr(message, 'sticker', None):
        kind = 'sticker'
        emoji = next((getattr(a, 'alt', '') for a in getattr(document,'attributes',[])
                      if getattr(a,'alt',None)), None)
    elif getattr(message, 'photo', None):
        kind, mime = 'image', 'image/jpeg'
    elif getattr(message, 'voice', None):
        kind, mime = 'voice', mime or 'audio/ogg'
    elif getattr(message, 'video', None) or getattr(message, 'video_note', None) or mime.startswith('video/'):
        kind, mime = 'video', mime or 'video/mp4'
    elif mime.startswith('image/'):
        kind = 'image'
    elif mime.startswith('audio/'):
        kind = 'audio'
    else:
        return None
    size = getattr(document, 'size', None) or getattr(file, 'size', None)
    duration = getattr(file, 'duration', None)
    return MediaDescriptor(kind, mime, size if isinstance(size,int) else None,
                           float(duration) if isinstance(duration,(float,int)) else None, emoji)
