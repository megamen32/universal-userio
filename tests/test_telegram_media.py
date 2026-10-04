"""Inbound classification and previews belong to UserIO, without network calls."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from universal_userio.channels.telegram import TelegramAPI
from universal_userio.channels.telegram_media import describe_media
from userio_adapter_sdk import AdapterNotSupported, Omnichannel


@pytest.mark.parametrize('mime,flags,kind', [
    ('', {'photo': True}, 'image'), ('audio/ogg', {'voice': True}, 'voice'),
    ('audio/mpeg', {}, 'audio'), ('video/mp4', {}, 'video'),
    ('video/mp4', {'video_note': True}, 'video'), ('image/png', {}, 'image'),
    ('image/webp', {'sticker': True}, 'sticker'),
    ('application/x-tgsticker', {'sticker': True}, 'sticker'),
    ('video/webm', {'sticker': True}, 'sticker'),
])
def test_descriptor(mime, flags, kind):
    raw = NS(document=NS(mime_type=mime, size=42, attributes=[NS(alt='🙂')]),
             file=NS(duration=3), **flags)
    result = describe_media(raw)
    assert result.kind == kind
    assert result.size_bytes == 42 and result.duration_seconds == 3
    assert result.emoji == ('🙂' if kind == 'sticker' else None)


def test_ordinary_document_keeps_provider_reference_metadata():
    raw = NS(
        document=NS(
            id=987654321,
            mime_type='application/pdf',
            size=4096,
            attributes=[NS(file_name='project-85m2.pdf')],
        ),
        file=NS(name='project-85m2.pdf', duration=None),
    )

    result = describe_media(raw)

    assert result is not None
    assert result.kind == 'document'
    assert result.mime_type == 'application/pdf'
    assert result.size_bytes == 4096
    assert result.file_name == 'project-85m2.pdf'
    assert result.document_id == '987654321'


def test_preview_download_uses_provider_thumbnail():
    raw = NS(id=12, download_media=AsyncMock())
    client = NS(download_media=AsyncMock(return_value=b'\x89PNGpreview'))
    result = asyncio.run(TelegramAPI(client).download_preview(42, raw))
    assert result.data == b'\x89PNGpreview' and result.mime_type == 'image/png'
    client.download_media.assert_awaited_once_with(raw, file=bytes, thumb=-1)


def test_missing_preview_is_explicit():
    raw = NS(id=12, download_media=AsyncMock())
    with pytest.raises(LookupError):
        asyncio.run(TelegramAPI(NS(download_media=AsyncMock(return_value=None))).download_preview(42, raw))


def test_preview_facade_honors_capabilities():
    api = Omnichannel()
    api.register(NS(platform='sms', capabilities=frozenset({'read', 'send'})))
    with pytest.raises(AdapterNotSupported):
        asyncio.run(api.download_preview('sms', 42, 12))
