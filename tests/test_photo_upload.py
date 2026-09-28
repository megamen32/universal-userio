"""Photo mode is a shared UserIO operation with explicit capabilities and receipts."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl.types import InputFile, InputPeerUser, UpdateMessageID
from userio_adapter_sdk import AdapterNotSupported, ChatOperationError, Omnichannel
from universal_userio.channels.capabilities import channel_capabilities
from universal_userio.channels.telegram import TelegramAPI


class Wire:
    def __init__(self):
        self.get_input_entity = AsyncMock(return_value=InputPeerUser(123, 456))
        self.upload_file = AsyncMock(return_value=InputFile(1, 1, 'photo.jpg', ''))
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(updates=[UpdateMessageID(88, request.random_id)])


def test_facade_photo_upload_uses_same_wire_identity_after_new_adapter():
    wire = Wire()
    async def run():
        for _ in range(2):
            router = Omnichannel()
            router.register(TelegramAPI(wire))
            result = await router.upload('telegram', 123, b'jpeg', filename='photo.jpg', mime_type='image/jpeg',
                                         caption='Photo', as_photo=True, idempotency_key='persisted-operation-1')
            assert result.id == 88 and result.media_type == 'image/jpeg'
    asyncio.run(run())
    assert wire.requests[0].random_id == wire.requests[1].random_id
    assert type(wire.requests[0].media).__name__ == 'InputMediaUploadedPhoto'
    assert wire.requests[0].message == 'Photo'


def test_missing_receipt_is_unknown_not_success():
    class NoReceipt(Wire):
        async def __call__(self, request):
            return SimpleNamespace(updates=[])
    with pytest.raises(ChatOperationError):
        asyncio.run(TelegramAPI(NoReceipt()).upload(123, b'jpeg', filename='photo.jpg', as_photo=True))


def test_facade_refuses_photo_on_generic_file_adapter():
    upload = AsyncMock()
    router = Omnichannel()
    router.register(SimpleNamespace(platform='test', capabilities={'upload'}, upload=upload))
    with pytest.raises(AdapterNotSupported):
        asyncio.run(router.upload('test', 1, b'image', filename='photo.jpg', as_photo=True))
    upload.assert_not_called()


def test_catalog_uses_implementation_capabilities():
    assert 'photo_upload' in channel_capabilities('telegram')
    for provider in ('sms','email','whatsapp','vk','unknown'):
        assert 'photo_upload' not in channel_capabilities(provider)
