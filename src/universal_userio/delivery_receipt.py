"""What one durable outbound receipt actually proves.

A receipt is the opaque string UserIO stores for one approved outbound
message. Most transports return a provider message id, so their receipt
proves delivery. SMTP does not: accepting a message at the SMTP server
proves only that the transport took it, never that the recipient got it.

The two must never be reported the same way, so a receipt states which of
them happened. A legacy opaque receipt keeps its old meaning (delivered),
because every provider that produced one also returned a provider message
id in it.

The Gmail receipt is ``himalaya:<account>:<draft_id>[:<message_id>[:<sent_copy>]]``.
The RFC Message-ID the transport accepted is the provider's own id for that
letter, and ``<sent_copy>`` names whether a copy was appended to the
account's Sent mailbox.
"""
from __future__ import annotations

import json
from typing import Any

SCHEMA = "userio.delivery.receipt.v1"

#: The provider confirmed the message with its own message id.
DELIVERED = "delivered"
#: The mail transport accepted the message; no provider delivery confirmation exists.
SMTP_ACCEPTED = "smtp_accepted"
#: The outcome is unknown; the message may or may not have reached the provider.
UNKNOWN = "unknown"
#: The transport provably never took the message.
NOT_SUBMITTED = "not_submitted"

STATUSES = frozenset({DELIVERED, SMTP_ACCEPTED, UNKNOWN, NOT_SUBMITTED})

_GMAIL_PREFIX = "himalaya:"
_SENT_COPY_STATES = {
    "saved": ("true", ""),
    "not_saved_command_failed": ("false", "sent_copy_command_failed"),
    "not_saved_rejected": ("false", "sent_copy_rejected"),
}


class DeliveryReceiptError(ValueError):
    """A receipt claims more than it can prove."""


def assert_truthful(status: str, confirmed: bool, message_id: str) -> None:
    """Refuse a delivery claim that cannot be proven.

    ``delivered`` needs both the confirmation flag and the provider's own
    message id. A receipt without them is a false delivery claim, which is
    exactly what makes a consumer report a letter as sent when it was not.
    """
    if status not in STATUSES:
        raise DeliveryReceiptError(f"unknown delivery status: {status or 'empty'}")
    if status == DELIVERED and not confirmed:
        raise DeliveryReceiptError("delivery_claimed_without_confirmation")
    if confirmed and not message_id.strip():
        raise DeliveryReceiptError("delivery_confirmed_without_provider_message_id")
    if confirmed and status != DELIVERED:
        raise DeliveryReceiptError("confirmed_delivery_with_wrong_status")


def read_receipt(receipt: str) -> dict[str, Any]:
    """Describe what a stored receipt proves.

    A Gmail receipt is read field by field. Any other receipt is a legacy
    provider string: it carried a provider message id, so it proves
    delivery exactly as it did before.
    """
    text = str(receipt or "").strip()
    if not text:
        return {
            "delivery_status": UNKNOWN, "delivery_confirmed": False,
            "message_id": "", "idempotency_key": "", "provider": "",
            "sent_copy": {}, "structured": False,
        }
    if text.startswith(_GMAIL_PREFIX):
        parts = text.split(":")
        account = parts[1] if len(parts) > 1 else ""
        draft_id = parts[2] if len(parts) > 2 else ""
        message_id = parts[3] if len(parts) > 3 else ""
        copy_state = parts[4] if len(parts) > 4 else ""
        saved, reason = _SENT_COPY_STATES.get(copy_state, ("", ""))
        return {
            "delivery_status": SMTP_ACCEPTED, "delivery_confirmed": False,
            "message_id": message_id, "idempotency_key": message_id,
            "provider": "gmail", "account": account, "draft_id": draft_id,
            "sent_copy": {"saved": saved, "mailbox": "sent", "reason": reason},
            "structured": True,
        }
    return {
        "delivery_status": DELIVERED, "delivery_confirmed": True,
        "message_id": "", "idempotency_key": "", "provider": "",
        "sent_copy": {}, "structured": False,
    }


def delivery_fields(receipt: str) -> dict[str, Any]:
    """The delivery facts of one stored receipt, ready for a tool answer.

    ``sent`` stays true only for a confirmed delivery. A message the
    transport merely accepted is reported as ``smtp_accepted`` with
    ``delivery_unconfirmed``, so a consumer cannot report it as delivered.
    """
    report = read_receipt(receipt)
    status = report["delivery_status"]
    return {
        "sent": bool(report["delivery_confirmed"]),
        "delivery_status": status,
        "delivery_confirmed": bool(report["delivery_confirmed"]),
        "delivery_unconfirmed": status == SMTP_ACCEPTED,
        "message_id": report["message_id"] or None,
        "idempotency_key": report["idempotency_key"] or None,
        "sent_copy": report.get("sent_copy") or {},
    }
