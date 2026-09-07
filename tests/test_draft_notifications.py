from universal_userio.contracts import ReplyDraft
from universal_userio.draft_notifications import TelegramDraftApprovalNotifier, format_draft_approval_notice


class Outbox:
    def __init__(self):
        self.calls = []

    def send_reply(self, **kwargs):
        self.calls.append(kwargs)
        return "telegram-qr:account-2:999:notice"


def test_format_single_draft_notice_has_context_preview_and_id():
    draft = ReplyDraft("draft_1", "conv_1", "Нормальный короткий ответ", "proposed")
    text = format_draft_approval_notice(
        {"source": "telegram", "sender": "Артём"}, [draft]
    )
    assert "Черновик ждёт подтверждения" in text
    assert "telegram: Артём" in text
    assert "Нормальный короткий ответ" in text
    assert "draft_1" in text


def test_telegram_notifier_is_owner_scoped_and_direct():
    outbox = Outbox()
    notifier = TelegramDraftApprovalNotifier(
        outbox, owner_user_id="owner-1", chat="Никита Розанов",
        chat_id="540308572", account_ref="telegram:8810909089",
    )
    draft = ReplyDraft("draft_2", "conv_2", "Ответ", "proposed")
    assert notifier.notify(
        user_id="other-user", conversation={"source": "telegram", "sender": "X"}, drafts=[draft]
    ) == ""
    assert outbox.calls == []

    receipt = notifier.notify(
        user_id="owner-1", conversation={"source": "telegram", "sender": "X"}, drafts=[draft]
    )
    assert receipt.startswith("telegram-qr:")
    assert len(outbox.calls) == 1
    call = outbox.calls[0]
    assert call["chat_id"] == "540308572"
    assert call["account_ref"] == "telegram:8810909089"
    assert call["draft_id"] == "draft-approval-notice:draft_2"
