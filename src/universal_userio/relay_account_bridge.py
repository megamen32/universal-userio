"""Exact-account UserIO reader and approved-reply route on an existing relay."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import urllib.request
from datetime import datetime
from functools import partial
from pathlib import Path

from .contracts import InboxMessage

SCHEMA = "userio.approved-source-reply.v1"
DOMAIN = SCHEMA + "\n"


def canonical_approval(approval: dict) -> bytes:
    return json.dumps(approval, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class RelayAccountBridge:
    """No client/session: use authenticated existing HTTP transport only."""
    def __init__(self, *, fallback, store, base_url: str, token_file: str,
                 approval_key: str, account_id: str, peer_id: str,
                 guard_file: str, opener=None):
        if not re.fullmatch(r"telegram:[1-9][0-9]*", account_id) or not re.fullmatch(r"[1-9][0-9]*", peer_id):
            raise ValueError("relay requires exact account and peer")
        if not approval_key or not base_url or not token_file or not guard_file:
            raise ValueError("relay bridge configuration is incomplete")
        self.fallback, self.store = fallback, store
        self.base_url, self.token_file = base_url.rstrip("/"), token_file
        self.approval_key = approval_key.encode("utf-8")
        self.account_id, self.peer_id, self.guard_file = account_id, peer_id, guard_file
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def source_bound_pair(self, account_ref: str, peer_id: str) -> bool:
        return (account_ref, peer_id) == (self.account_id, self.peer_id)

    def _request(self, method: str, path: str, payload=None) -> dict:
        token = Path(self.token_file).read_text().strip()
        if not token:
            raise ValueError("relay credential is unavailable")
        request = urllib.request.Request(self.base_url + path, method=method,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with self.opener(request, timeout=10) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("relay response exceeds limit")
            result = json.loads(raw)
        if not isinstance(result, dict) or result.get("ok") is False or result.get("error"):
            raise ValueError("relay operation refused")
        return result

    def _scope_expiry(self) -> int:
        now = time.time()
        local = json.loads(Path(self.guard_file).read_text())
        if (local.get("source") != "telegram" or not self.source_bound_pair(
                local.get("account_ref", ""), local.get("peer_id", ""))
                or not local.get("purpose") or not local["issued_at"] <= now < local["expires_at"]):
            raise ValueError("UserIO exact-pair guard is not active")
        health = self._request("GET", "/health")
        if str(health.get("accountId")) != self.account_id.removeprefix("telegram:"):
            raise ValueError("relay account identity mismatch")
        bridge = health.get("userioBridge") or {}
        if (bridge.get("active") is not True or (bridge.get("account_id"), bridge.get("peer_id")) != (
                self.account_id, self.peer_id)):
            raise ValueError("maintained UserIO relay bridge is not active")
        matches = [rule for rule in health.get("inboundExclusions", [])
                   if str(rule.get("accountId")) == self.account_id.removeprefix("telegram:")
                   and str(rule.get("peerId")) == self.peer_id and rule.get("purpose") == bridge.get("purpose")]
        ends = [datetime.fromisoformat(rule["expiresAt"].replace("Z", "+00:00")).timestamp()
                for rule in matches]
        if not ends or max(ends) <= now:
            raise ValueError("relay exact-pair guard is not active")
        bridge_expiry = datetime.fromisoformat(bridge["expires_at"].replace("Z", "+00:00")).timestamp()
        if bridge_expiry <= now:
            raise ValueError("relay bridge scope expired")
        return int(min(local["expires_at"], max(ends), bridge_expiry))

    def read_page(self, after: int = 0) -> dict:
        if type(after) is not int or after < 0:
            raise ValueError("invalid relay cursor")
        self._scope_expiry()
        page = self._request("GET", f"/userio/telegram/events?after={after}")
        if (page.get("account_id"), page.get("peer_id"), page.get("cursor")) != (
                self.account_id, self.peer_id, after):
            raise ValueError("relay projection scope mismatch")
        events, next_cursor = page.get("events"), page.get("next_cursor")
        if not isinstance(events, list) or len(events) > 200 or type(next_cursor) is not int or next_cursor < after:
            raise ValueError("invalid relay projection page")
        previous = after
        for event in events:
            seq = event.get("seq")
            if (type(seq) is not int or not previous < seq <= next_cursor
                    or event.get("direction") != "incoming"
                    or str(event.get("sender_id")) != self.peer_id
                    or not re.fullmatch(r"[1-9][0-9]*", str(event.get("provider_message_id", "")))
                    or not isinstance(event.get("text"), str) or not event["text"]):
                raise ValueError("invalid real relay source event")
            previous = seq
        return page

    def sync_into(self, service, *, user_id: str) -> dict:
        """Explicit bounded operator sync; no background poll or invented row."""
        if user_id != self.store.default_user_id:
            raise PermissionError("relay scope belongs to the service owner")
        account = next((a for a in self.store.accounts(user_id=user_id)
                        if a["id"] == self.account_id and a["enabled"] and "read" in a["capabilities"]), None)
        if account is None:
            raise PermissionError("relay account is not assigned to this owner")
        cursor_key = f"relay_source_cursor:{self.account_id}:{self.peer_id}"
        after = int(self.store.user_preference(cursor_key, user_id=user_id, default="0") or 0)
        page = self.read_page(after)
        conversations = set()
        for event in page["events"]:
            native_at = event["provider_at"]
            if isinstance(native_at, str):
                if not native_at.endswith("Z"):
                    raise ValueError("provider timestamp must be UTC")
                native_at = datetime.fromisoformat(native_at.replace("Z", "+00:00")).timestamp()
            if type(native_at) not in (int, float) or not math.isfinite(native_at) or native_at <= 0:
                raise ValueError("invalid provider timestamp")
            message = InboxMessage(source="telegram", message_id=f"{self.peer_id}:{event['provider_message_id']}",
                sender=self.peer_id, peer_id=self.peer_id, body=event["text"], received_at=native_at,
                direction="incoming", conversation_kind="direct", reconciliation=True)
            cid, _ = service.receive(message, route_id="telegram", user_id=user_id, account_ref=self.account_id)
            conversations.add(cid)
        self.store.set_user_preference(cursor_key, str(page["next_cursor"]), user_id=user_id)
        return {"account_id": self.account_id, "peer_id": self.peer_id,
                "imported": len(page["events"]), "conversation_ids": sorted(conversations),
                "next_cursor": page["next_cursor"]}

    def prepare_reply(self, *, chat: str, chat_id: str, account_ref: str,
                      body: str, draft_id: str, user_id: str, conversation_id: str):
        if not self.source_bound_pair(account_ref, chat_id):
            return partial(self.fallback.send_reply, chat=chat, chat_id=chat_id,
                           account_ref=account_ref, body=body, draft_id=draft_id)
        if user_id != self.store.default_user_id:
            raise PermissionError("relay scope belongs to the service owner")
        origin = self.store.draft_reply_origin(draft_id, user_id=user_id)
        if (origin["account_ref"], origin["peer_id"], origin["conversation_id"]) != (
                account_ref, chat_id, conversation_id):
            raise ValueError("draft source identity conflict")
        draft = self.store.draft(draft_id, user_id=user_id)
        if draft.status != "sending" or draft.body != body:
            raise ValueError("authoritative approval claim is missing")
        expiry = self._scope_expiry()
        now = int(time.time())
        approval = {"schema": SCHEMA, "account_id": account_ref, "peer_id": chat_id,
            "user_id": user_id, "conversation_id": conversation_id, "draft_id": draft_id,
            "source_message_id": origin["provider_message_id"],
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "idempotency_key": f"userio:{user_id}:{draft_id}", "approved_by": user_id,
            "issued_at": now, "expires_at": min(now + 300, expiry), "nonce": draft_id}
        proof = hmac.new(self.approval_key, DOMAIN.encode() + canonical_approval(approval), hashlib.sha256).hexdigest()
        return partial(self._send, body, approval, proof)

    def _send(self, text: str, approval: dict, proof: str) -> str:
        if int(time.time()) >= approval["expires_at"]:
            raise ValueError("approval expired before dispatch")
        result = self._request("POST", "/userio/telegram/reply", {"text": text, "approval": approval, "proof": proof})
        provider_id = str(result.get("providerMessageId", ""))
        if not re.fullmatch(r"[1-9][0-9]*", provider_id) or type(result.get("duplicate")) is not bool:
            raise RuntimeError("relay delivery has no durable receipt")
        return f"exmanager-relay:{self.account_id}:{self.peer_id}:{provider_id}:{approval['draft_id']}"

    def send_reply(self, **kwargs):
        if self.source_bound_pair(kwargs.get("account_ref", ""), kwargs.get("chat_id", "")):
            raise ValueError("explicit source-bound UserIO approval is required")
        return self.fallback.send_reply(**kwargs)

    def react(self, **kwargs):
        return self.fallback.react(**kwargs)
