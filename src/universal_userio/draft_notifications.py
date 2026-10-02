"""System notifications for reply drafts that still need owner approval."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import ReplyDraft


def _preview(text: str, limit: int = 420) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def format_draft_approval_notice(
    conversation: Mapping[str, Any], drafts: Sequence[ReplyDraft],
) -> str:
    drafts = [draft for draft in drafts if draft.status == "proposed"]
    if not drafts:
        raise ValueError("approval notice requires at least one proposed draft")
    source = str(conversation.get("source") or "UserIO").strip() or "UserIO"
    title = str(
        conversation.get("display_name")
        or conversation.get("identity_id")
        or conversation.get("sender")
        or conversation.get("id")
        or "диалог"
    ).strip()
    if len(drafts) == 1:
        draft = drafts[0]
        return (
            "📝 Черновик ждёт подтверждения\n"
            f"{source}: {title}\n\n"
            f"{_preview(draft.body)}\n\n"
            f"ID: {draft.id}\n"
            "Подтверди или отредактируй его в UserIO."
        )
    lines = [
        f"📝 {len(drafts)} варианта черновика ждут подтверждения",
        f"{source}: {title}",
        "",
    ]
    for index, draft in enumerate(drafts, start=1):
        lines.append(f"{index}. {_preview(draft.body, 220)}")
        lines.append(f"   ID: {draft.id}")
    lines.extend(["", "Выбери, отредактируй и подтверди нужный вариант в UserIO."])
    return "\n".join(lines)


class TelegramDraftApprovalNotifier:
    """Send a system alert to the owner's Telegram DM without creating another draft."""

    def __init__(
        self, outbox: object, *, owner_user_id: str, chat: str, chat_id: str, account_ref: str,
    ) -> None:
        self._outbox = outbox
        self._owner_user_id = str(owner_user_id)
        self._chat = str(chat).strip()
        self._chat_id = str(chat_id).strip()
        self._account_ref = str(account_ref).strip()
        if not self._chat or not self._chat_id or not self._account_ref:
            raise ValueError("draft notifier requires Telegram chat, chat id and account ref")

    def notify(
        self, *, user_id: str, conversation: Mapping[str, Any], drafts: Sequence[ReplyDraft],
    ) -> str:
        if str(user_id) != self._owner_user_id:
            return ""
        proposed = [draft for draft in drafts if draft.status == "proposed"]
        if not proposed:
            return ""
        body = format_draft_approval_notice(conversation, proposed)
        first = proposed[0].id
        return self._outbox.send_reply(
            chat=self._chat, chat_id=self._chat_id, account_ref=self._account_ref,
            body=body, draft_id=f"draft-approval-notice:{first}",
        )
