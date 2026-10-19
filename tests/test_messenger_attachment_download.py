"""Source-bound document bytes through the existing UserIO media consumer."""
import base64
import hashlib

import pytest

from universal_userio.contracts import InboxMessage, UserPrincipal
from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.messenger import Messenger
from universal_userio.messenger_max import MaxMessengerProvider
from universal_userio.messenger_telegram_mirror import TelegramMirrorProvider
from universal_userio.contracts import ChannelFile
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.max_ingress import message_envelope
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class MaxFixture:
    profile = {"profile": {"contact": {"id": 77}}}
    authentication_required = False

    def __init__(self, attachments):
        self.attachments = attachments
        self.media_reads = []

    def get_message(self, *, chat_id, message_id):
        assert (chat_id, message_id) == ("42", "281")
        return {"id": 281, "sender": 78, "text": "Документы", "time": 1700000000000,
                "attaches": self.attachments}

    def get_chat(self, peer_id):
        return {"id": int(peer_id), "type": "GROUP"}

    def media_reference(self, *, chat_id, message_id, attachment):
        self.media_reads.append((chat_id, message_id, attachment["fileId"]))
        return {"url": f"https://file.mycdn.me/{attachment['fileId']}"}


def test_max_ingress_keeps_native_filename_mime_size_and_provider_attachment_id():
    envelope=message_envelope({"id":281,"sender":78,"text":"file",
        "time":1700000000000,"attaches":[{"_type":"FILE","fileId":20,
        "fileName":"Заявка.xlsx","mimeType":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "size":504260}]},chat_id="42",own_id="77")
    assert envelope["attachments"]==[{
        "kind":"file","provider_ref":{"chat_id":"42","message_id":"281"},
        "attachment_id":20,"filename":"Заявка.xlsx",
        "content_type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "size":504260,
    }]


def test_max_document_media_returns_exact_selected_original_bytes(tmp_path, monkeypatch):
    data = b"PK\x03\x04docx-bytes"
    attaches = [
        {"_type": "FILE", "name": "other.pdf", "fileId": 19, "size": 4},
        {"_type": "FILE", "name": "Заявка.xlsx", "fileId": 20, "size": len(data),
         "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ]
    client = MaxFixture(attaches)
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, object(), object(), max_client=client,
                            max_user_id=store.default_user_id)
    account = "max"
    store.register_account(account_id=account, provider="max", display_name="MAX",
                           can_read=True, can_reply=True, credential_ref="env:fixture")
    conversation_id, _ = service.receive(InboxMessage(
        "max", "max-42-281", "78", "Документы", 1700000000,
        peer_id="42", conversation_kind="group",
        attachments=tuple({"kind": "file", "filename": item["name"], "size": item["size"],
                           "attachment_id": item["fileId"],
                           "idx": index} for index, item in enumerate(attaches)),
    ), route_id="max", account_ref=account)
    provider = MaxMessengerProvider(service)
    service.messenger_providers = {account: provider}
    monkeypatch.setattr("universal_userio.messenger_max_media.fetch_native_url",
                        lambda url, **_kwargs: (data, "application/octet-stream"))

    result = Messenger(store, service, UserPrincipal(store.default_user_id, "owner", "owner")).media(
        account, conversation_id, "max-42-281", attachment_index=1)

    content = result["content"]
    assert content["status"] == "ready"
    assert content["filename"] == "Заявка.xlsx"
    assert content["mimeType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert content["bytes"] == len(data)
    assert content["sha256"] == hashlib.sha256(data).hexdigest()
    assert base64.b64decode(content["dataBase64"]) == data
    assert client.media_reads == [("42", "281", 20)]

    monkeypatch.setattr("universal_userio.matrix_max_media.fetch_native_url",
                        lambda url, **_kwargs: (data, "application/octet-stream"))
    downloaded = UserIOMcpSurface(store, service).dispatch("userio.channels.download", {
        "file_ref": "max:max-42-281", "attachment_index": 1,
    })
    assert "file" in downloaded, downloaded
    mcp_file = downloaded["file"]
    assert mcp_file["filename"] == "Заявка.xlsx"
    assert mcp_file["content_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert mcp_file["size"] == len(data)
    assert mcp_file["sha256"] == hashlib.sha256(data).hexdigest()
    assert base64.b64decode(mcp_file["data"]) == data
    store.close()


def test_max_document_media_refuses_wrong_owner_and_bad_attachment_index(tmp_path, monkeypatch):
    client = MaxFixture([{"_type": "FILE", "name": "x.pdf", "fileId": 20, "size": 3}])
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, object(), object(), max_client=client,
                            max_user_id=store.default_user_id)
    store.register_account(account_id="max", provider="max", display_name="MAX",
                           can_read=True, can_reply=True, credential_ref="env:fixture")
    cid, _ = service.receive(InboxMessage("max", "max-42-281", "78", "x", 1700000000,
        peer_id="42", conversation_kind="group", attachments=({"kind": "file", "filename": "x.pdf", "idx": 0},)),
        route_id="max", account_ref="max")
    service.messenger_providers = {"max": MaxMessengerProvider(service)}
    monkeypatch.setattr("universal_userio.messenger_max_media.fetch_native_url",
                        lambda *_args, **_kwargs: (b"pdf", "application/pdf"))
    owner = Messenger(store, service, UserPrincipal(store.default_user_id, "owner", "owner"))
    with pytest.raises(ValueError, match="invalid_attachment_index"):
        owner.media("max", cid, "max-42-281", attachment_index=1)
    other, _ = store.create_user("other", "safe-password")
    outsider = Messenger(store, service, UserPrincipal(other.user_id, "other", "owner"))
    with pytest.raises((PermissionError, KeyError)):
        outsider.media("max", cid, "max-42-281", attachment_index=0)
    rejected=UserIOMcpSurface(store,service).dispatch("userio.channels.download",{
        "file_ref":"https://files.example/private.xlsx",
    })
    assert rejected["ok"] is False
    assert client.media_reads == []
    store.close()


def test_telegram_media_consumer_downloads_exact_stored_message_file(tmp_path, monkeypatch):
    data = b"PK\x03\x04telegram-xlsx"
    store = SQLiteUserIOStore(tmp_path / "telegram.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:900"
    store.register_account(account_id=account, provider="telegram", display_name="Nikita",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    message_id = f"{account}|-1003724214344:281"
    conversation_id, _ = service.receive(InboxMessage(
        "telegram", message_id, "Никита, Oleg и Aslan", "Документ", 1700000000,
        peer_id="-1003724214344", conversation_kind="group",
        attachments=({"kind": "document", "filename": "Заявка.xlsx",
                     "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     "size": len(data), "idx": 0},),
    ), route_id="telegram", account_ref=account)
    provider = TelegramMirrorProvider(service, account, store.default_user_id)
    service.messenger_providers = {account: provider}
    reads = []
    monkeypatch.setattr(TelegramChannelAdapter, "download", lambda _self, **kwargs:
                        reads.append(kwargs) or ChannelFile("Заявка.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", data))

    result = Messenger(store, service, UserPrincipal(store.default_user_id, "owner", "owner")).media(
        account, conversation_id, message_id, attachment_index=0)

    content = result["content"]
    assert content["status"] == "ready"
    assert content["filename"] == "Заявка.xlsx"
    assert content["mimeType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert content["bytes"] == len(data)
    assert content["sha256"] == hashlib.sha256(data).hexdigest()
    assert base64.b64decode(content["dataBase64"]) == data
    assert reads == [{"file_ref": message_id, "attachment_index": 0,
                      "max_bytes": 4 * 1024 * 1024}]
    assert result["provider_read_acknowledged"] is False
    store.close()
