# Archived VK native account adapter (inactive)

The following functions were extracted verbatim from release ae7dc0a.
They are NOT imported into active UserIO. Requires VK API messages permission.

## UserAccountPoller.poll_vk

```python
def poll_vk(self, account: Mapping[str, str]) -> None:
        from .channels.vk_native import VKNativeReader
        user = str(account["user_id"])
        account_id = str(account["account_id"])
        token = str(account["access_token"])
        reader = VKNativeReader(token)
        page_key = "vk:dialog-page:" + account_id
        offset = int(self.service._store.user_preference(page_key, user_id=user) or "0")
        page = reader.conversations(offset=offset, count=40)
        results = page["items"]
        user_names: dict[int, str] = {}
        for profile in page.get("profiles") or []:
            if isinstance(profile, dict) and type(profile.get("id")) is int:
                user_names[profile["id"]] = (
                    str(profile.get("first_name") or "") + " " +
                    str(profile.get("last_name") or "")
                ).strip()[:120]
        processed = 0
        next_offset = offset + len(results)
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            peer = (item.get("conversation") or {}).get("peer") or {}
            peer_id = peer.get("id")
            latest = item.get("last_message") or {}
            if type(peer_id) is not int or peer_id == 0:
                continue
            key = f"vk:message-cursor:{account_id}:{peer_id}"
            previous = int(self.service._store.user_preference(key, user_id=user) or "0")
            latest_id = latest.get("id") or (item.get("conversation") or {}).get("last_message_id")
            if type(latest_id) is not int or latest_id <= previous:
                continue
            if processed >= 5:
                # Resume exactly at the first unprocessed dialog, rather than
                # skipping an account's less-active conversations.
                next_offset = offset + index
                break
            messages = reader.history(peer_id, count=40)
            # VK returns newest first. Older pages are needed if a busy chat
            # accumulated many messages while UserIO was offline.
            page_number = 1
            while previous and len(messages) >= page_number * 40:
                earliest = min(
                    (m["id"] for m in messages if type(m.get("id")) is int),
                    default=0,
                )
                if earliest <= previous:
                    break
                if page_number >= 5:
                    # Never advance a cursor across an unverified history gap.
                    raise RuntimeError("VK history catch-up requires more pages")
                older = reader.history(peer_id, count=40, offset=page_number * 40)
                messages.extend(older)
                page_number += 1
                if len(older) < 40:
                    break
            settings = (item.get("conversation") or {}).get("chat_settings") or {}
            title = str(settings.get("title") or "") if isinstance(settings, dict) else ""
            if not title:
                title = user_names.get(peer_id, f"VK {peer_id}")
            for row in sorted(messages, key=lambda m: m.get("id") or 0):
                if type(row.get("id")) is not int or row["id"] <= previous:
                    continue
                envelope = vk_conversation_message(
                    account_id=account_id, peer_id=peer_id, row=row,
                    title=title, reconciled=not previous,
                )
                if envelope is not None:
                    self.service.receive(
                        envelope, route_id="vk-read-only", user_id=user,
                        account_ref=account_id,
                    )
            self.service._store.set_user_preference(
                key, str(max((m["id"] for m in messages if type(m.get("id")) is int),
                             default=previous)), user_id=user,
            )
            processed += 1
        # Rotate pages fairly; newly active dialogs normally appear first.
        total = int(page.get("count") or len(results))
        self.service._store.set_user_preference(
            page_key, str(next_offset if results and next_offset < total else 0),
            user_id=user,
        )
```

## connect_vk_direct

```python
def connect_vk_direct(
    service: UserIOService, user_id: str, payload: Mapping[str, Any],
    *, vault: ConnectorSecrets | None = None, reader_factory: Any = None,
) -> dict[str, str]:
    """Enable VK personal messages only after the actual message permission is proven."""
    from .channels.vk_native import VKNativeReader

    token = str(payload.get("access_token") or "")
    expected = str(payload.get("user_id") or "").strip()
    if expected and (not expected.isdecimal() or len(expected) > 20):
        raise ValueError("неверный VK ID")
    reader = (reader_factory or VKNativeReader)(token)
    actual = reader.identity()
    if expected and int(expected) != actual:
        raise ValueError("токен принадлежит другому аккаунту VK")
    dialogs = reader.conversations(count=1)
    if dialogs["items"]:
        first_peer = ((dialogs["items"][0].get("conversation") or {}).get("peer") or {}).get("id")
        if type(first_peer) is int and first_peer:
            reader.history(first_peer, count=1)
    # The legacy VK ID account is upgraded only once both methods succeed.
    account_id = f"vk:{actual}"
    storage = vault or ConnectorSecrets()
    reference = storage.save({
        "provider": "vk", "user_id": user_id, "account_id": account_id,
        "vk_user_id": str(actual), "access_token": token,
    })
    service._store.register_account(
        account_id=account_id, provider="vk",
        display_name=str(payload.get("display_name") or f"VK {actual}")[:120],
        can_read=True, can_reply=False, credential_ref=reference, user_id=user_id,
    )
    service._store.bind_channel_route(user_id=user_id, source="vk", route_id="vk-read-only")
    return {"id": account_id, "source": "vk", "display_name": str(payload.get("display_name") or f"VK {actual}")[:120],
            "mode": "vk_personal_messages_readonly"}
```

## vk_conversation_message

```python
def vk_conversation_message(
    *, account_id: str, peer_id: int, row: Mapping[str, Any],
    title: str = "", reconciled: bool,
) -> InboxMessage | None:
    """VK message envelope, retaining actual peer and local account identity."""
    message_id = row.get("id")
    received = row.get("date")
    if type(message_id) is not int or message_id <= 0:
        return None
    if type(received) is not int or received <= 0:
        return None
    body = str(row.get("text") or "")
    if not body and row.get("attachments"):
        body = "[Вложение VK]"
    if not body:
        return None
    outgoing = row.get("out") is True or row.get("out") == 1
    peer = str(peer_id)
    return InboxMessage(
        source="vk", message_id=f"{account_id}|{peer}:{message_id}",
        sender=peer, sender_name=title, body=body[:50_000],
        received_at=float(received), direction="outgoing" if outgoing else "incoming",
        peer_id=peer, conversation_kind="group" if peer_id >= 2_000_000_000 else "direct",
        conversation_title=title, reconciliation=reconciled,
    )
```
