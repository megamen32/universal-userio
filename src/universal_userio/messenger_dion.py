"""DION operations through the existing UserIO approval and account boundary."""
from __future__ import annotations

import base64
import hashlib

from .contracts import DeliveryNotSubmittedError
from .dion_chat import (_client, _owned_message, download_owned_attachment,
                        dion_message, DionChatUncertain, DionChatRejected,
                        reconcile_dion_receipt, _PROVIDER_ID)


class DionMessengerProvider:
    operations = frozenset({"reply", "file", "file_upload", "download"})

    def __init__(self, service, account_ref, user_id):
        self.service = service
        self.account_ref = account_ref
        self.authorized_user = user_id

    @property
    def sender_id(self):
        return self.account_ref.rsplit(":", 1)[-1]

    def upload_limit(self):
        from .dion_chat import _MAX_FILE
        return _MAX_FILE

    def media(self, projected, *, attachment_index=0):
        source = projected["source"]
        if source["account_ref"] != self.account_ref:
            raise PermissionError("DION account mismatch")
        file = download_owned_attachment(self.service, self.authorized_user,
                                         source["message_id"],
                                         attachment_index=attachment_index,
                                         max_bytes=4 * 1024 * 1024,
                                         account_id=self.account_ref)
        return {"status": "ready", "partial": False, "filename": file.filename,
                "mimeType": file.content_type, "bytes": len(file.data),
                "sha256": hashlib.sha256(file.data).hexdigest(),
                "dataBase64": base64.b64encode(file.data).decode("ascii")}

    def _preflight(self, e, client, own_id):
        local, conversation, raw_id = _owned_message(
            self.service, self.authorized_user, e["source_message_id"],
            account_id=self.account_ref)
        if (str(conversation["id"]) != e["conversation_id"]
                or str(conversation["peer_id"]) != e["peer_id"]
                or raw_id != e["source_provider_message_id"]):
            raise ValueError("DION source scope conflict")
        row = client.message(raw_id)
        message = dion_message(row, account_id=self.account_ref, own_user_id=own_id)
        if (message is None or message.message_id != local["message_id"]
                or message.peer_id != e["peer_id"]):
            raise ValueError("DION source missing")
        expected = e["source_binding"]
        actual = {
            "account_ref": self.account_ref,
            "peer_id": message.peer_id,
            "provider_message_id": raw_id,
            "direction": message.direction,
            "author_id": message.sender,
            "edited_at": int(message.edited_at or 0),
            "text_sha256": hashlib.sha256(message.body.encode("utf-8")).hexdigest(),
            "thread_id": "",
            "reply_to_provider_message_id": message.provider_reply_to,
        }
        if expected != actual:
            raise ValueError("DION source changed before dispatch")
        return conversation, raw_id

    def execute(self, e):
        if e["user_id"] != self.authorized_user or e["account_ref"] != self.account_ref:
            raise PermissionError("DION account route mismatch")
        if e["action"] not in {"reply", "file"}:
            raise DeliveryNotSubmittedError("DION action not supported")
        payload = e["payload"]
        try:
            client, secret = _client(self.service, self.authorized_user, self.account_ref)
            conversation, source_id = self._preflight(e, client, str(secret.get("provider_user_id") or ""))
            info = client.conversation(e["peer_id"])
            if "conversation.messages.create" not in set(info.get("permissions") or []):
                raise ValueError("DION conversation cannot send")
            if e["action"] == "reply":
                text, data, filename, mime = payload["text"], None, "", ""
            else:
                if payload.get("force_document"):
                    raise ValueError("DION force_document is not supported")
                text = payload.get("caption", "")
                if "upload_id" in payload:
                    from .messenger_uploads import MessengerUploads
                    data = MessengerUploads(self.service._store, self.authorized_user).data(e)
                    filename, mime = payload["filename"], payload["mime_type"]
                else:
                    local, media_conv, _ = _owned_message(
                        self.service, self.authorized_user, payload["media_message_id"],
                        account_id=self.account_ref)
                    if media_conv["id"] != conversation["id"]:
                        raise ValueError("DION media scope mismatch")
                    file = download_owned_attachment(self.service, self.authorized_user,
                        payload["media_message_id"], attachment_index=payload["attachment_index"],
                        account_id=self.account_ref)
                    data, filename, mime = file.data, file.filename, file.content_type
                if not data or len(data) > self.upload_limit():
                    raise ValueError("DION staged attachment is invalid")
        except Exception as exc:
            raise DeliveryNotSubmittedError("DION preflight failed; no message or file submitted") from exc
        # An approved action uses a stable intermediate ID. Never send again
        # after an ambiguous upload or message result.
        attachment_ids = ()
        if data is not None:
            attachment = client.upload_attachment(data, filename=filename, mime_type=mime)
            attachment_ids = (str(attachment["id"]),)
        try:
            receipt = client.send_text(e["peer_id"], text, request_id=e["action_id"],
                                       attachment_ids=attachment_ids, reply_to=source_id)
        except DionChatRejected as exc:
            if data is not None:
                raise DionChatUncertain("DION attachment uploaded; message not confirmed") from exc
            raise
        _, _, message_id = client.parse_receipt(receipt)
        if not _PROVIDER_ID.fullmatch(message_id):
            raise DionChatUncertain("DION message receipt is not yet visible")
        return {"account_ref": self.account_ref, "peer_id": e["peer_id"],
                "provider_message_id": message_id, "receipt": receipt}

    def project_receipt(self, e, result):
        conversation = self.service._store.conversation(e["conversation_id"],
                                                          user_id=self.authorized_user)
        if not conversation or conversation["account_ref"] != self.account_ref:
            raise ValueError("DION conversation unavailable")
        reconcile_dion_receipt(self.service, conversation, result["receipt"],
                               user_id=self.authorized_user)
