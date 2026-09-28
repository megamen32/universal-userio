"""Telegram implementation of the shared FilePort photo-upload operation."""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

from telethon import utils
from telethon.errors import FloodWaitError, ForbiddenError, PeerFloodError
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaUploadedPhoto, UpdateMessageID, UpdateShortSentMessage
from userio_adapter_sdk import ChatInvalidPeerError, ChatMessage, ChatOperationError, ChatPermissionError, ChatRateLimitError


async def upload_photo(client: Any, target: Any, data: bytes, *, caption: str,
                       idempotency_key: str | None = None) -> ChatMessage:
    """Send an image, preserving the caller's operation identity on retries.

    The caller persists immutable bytes, recipient and key. A lost receipt remains
    unknown; Telegram's deduplication window is not an indefinite delivery guarantee.
    """
    if idempotency_key is not None and not idempotency_key.strip():
        raise ValueError('idempotency_key must not be blank')
    if not data or len(caption.encode('utf-16-le')) // 2 > 1024:
        raise ValueError('A photo and a caption of at most 1024 characters are required')
    try:
        peer = await client.get_input_entity(target)
    except (TypeError, ValueError) as exc:
        raise ChatInvalidPeerError('Photo recipient could not be resolved before sending') from exc
    if idempotency_key is None:
        random_id = secrets.randbits(63) or 1
    else:
        digest = hashlib.sha256(b'userio:photo:v1\0' + idempotency_key.encode()).digest()
        random_id = int.from_bytes(digest[:8], 'big', signed=True) or 1
    try:
        uploaded = await client.upload_file(data, file_name='photo.jpg')
        result = await client(SendMediaRequest(peer=peer, media=InputMediaUploadedPhoto(file=uploaded),
                                               message=caption, random_id=random_id))
    except ForbiddenError as exc:
        raise ChatPermissionError(str(exc)) from exc
    except (FloodWaitError, PeerFloodError) as exc:
        error = ChatRateLimitError(str(exc))
        error.seconds = getattr(exc, 'seconds', None)
        raise error from exc
    message_id = result.id if isinstance(result, UpdateShortSentMessage) else None
    if message_id is None:
        identifiers = {update.id for update in getattr(result, 'updates', ())
                       if isinstance(update, UpdateMessageID) and update.random_id == random_id}
        if len(identifiers) == 1:
            message_id = identifiers.pop()
    if not isinstance(message_id, int) or message_id <= 0:
        raise ChatOperationError('Telegram did not return a receipt for this photo upload')
    return ChatMessage(chat_id=utils.get_peer_id(peer), message_id=message_id, text=caption,
                       date=getattr(result, 'date', None), media_type='image/jpeg', out=True)
