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
    file_name: str | None = None
    document_id: str | None = None


def describe_media(message: Any) -> MediaDescriptor | None:
    """Describe Telegram media, including retrievable ordinary documents."""
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
    elif document is not None:
        kind = 'document'
    else:
        return None
    size = getattr(document, 'size', None) or getattr(file, 'size', None)
    duration = getattr(file, 'duration', None)
    file_name = getattr(file, 'name', None)
    if not file_name:
        file_name = next(
            (
                getattr(attribute, 'file_name', None)
                for attribute in getattr(document, 'attributes', [])
                if getattr(attribute, 'file_name', None)
            ),
            None,
        )
    document_id = getattr(document, 'id', None)
    return MediaDescriptor(
        kind=kind,
        mime_type=mime,
        size_bytes=size if isinstance(size, int) else None,
        duration_seconds=float(duration) if isinstance(duration, (float, int)) else None,
        emoji=emoji,
        file_name=str(file_name) if file_name else None,
        document_id=str(document_id) if document_id is not None else None,
    )
