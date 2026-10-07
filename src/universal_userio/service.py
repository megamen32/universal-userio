"""Business use cases; AI proposes and approval is the only send authority."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial

from .contracts import DraftGenerator, InboxMessage, OutboxClient, ReplyDraft
from .store import SQLiteUserIOStore


_LOG = logging.getLogger(__name__)
_AI_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_AI_IMAGE_MAX_BYTES = 2 * 1024 * 1024
_AI_IMAGE_MAX_COUNT = 4


class DeliveryUnavailableError(ValueError):
    """The configured source intentionally has no outbound delivery capability."""


class UserIOService:
    def __init__(
        self, store: SQLiteUserIOStore, generator: DraftGenerator, outbox: OutboxClient,
        *, sms_gateway: object | None = None, sms_user_id: str = "", sms_route_id: str = "sms",
        sms_manual_approve: bool = False,
        max_client: object | None = None, max_user_id: str = "", max_route_id: str = "max",
        max_manual_approve: bool = False,
        gmail_outbox: object | None = None,
        chatgpt_outbox: object | None = None,
        telegram_outbox: object | None = None,
        whatsapp_outbox: object | None = None,
        draft_notifier: object | None = None,
        draft_notification_delay_seconds: float = 5.0,
        ai_image_loader: Callable[..., object | None] | None = None,
    ) -> None:
        self._store = store
        self._generator = generator
        self._outbox = outbox
        self._user_generators: dict[str, object] = {}
        self.sms_gateway, self.sms_user_id, self.sms_route_id = sms_gateway, sms_user_id, sms_route_id
        self.sms_manual_approve = bool(sms_manual_approve)
        self.max_client, self.max_user_id, self.max_route_id = max_client, max_user_id, max_route_id
        self.max_manual_approve = bool(max_manual_approve)
        self.gmail_outbox = gmail_outbox
        self.chatgpt_outbox = chatgpt_outbox
        self.telegram_outbox = telegram_outbox
        self.whatsapp_outbox = whatsapp_outbox
        self.draft_notifier = draft_notifier
        self.draft_notification_delay_seconds = max(0.0, float(draft_notification_delay_seconds))
        self._ai_image_loader = ai_image_loader or self._download_ai_image
        self._inbound_listeners: list[object] = []

    def _download_ai_image(
        self, *, source: str, message_id: str,
        attachment: dict[str, object], user_id: str,
    ) -> object | None:
        """Load one image through the account-scoped channel adapter.

        Provider URLs and references remain inside the adapter boundary.  The
        AI receives only validated bytes added by ``_hydrate_ai_images``.
        """
        from .adapters import TelegramChannelAdapter, WhatsAppChannelAdapter
        from .channels.core import AdapterNotSupported

        key = source.split(":", 1)[0]
        adapters = {
            "telegram": TelegramChannelAdapter,
            "whatsapp": WhatsAppChannelAdapter,
        }
        adapter_type = adapters.get(key)
        if adapter_type is None:
            return None
        file_ref = message_id
        try:
            adapter = adapter_type(self._store, self, user_id)
            bounded = getattr(adapter, "download_for_ai", None)
            if not callable(bounded):
                return None
            return bounded(file_ref=file_ref, max_bytes=_AI_IMAGE_MAX_BYTES)
        except (AdapterNotSupported, KeyError, ValueError, OSError):
            return None

    def _hydrate_ai_images(
        self, entries: Sequence[dict[str, object]], *, user_id: str,
        image_budget: list[int] | None = None,
    ) -> list[dict[str, object]]:
        """Attach bounded image bytes ephemerally; never persist provider URLs."""
        if image_budget is not None and (
            len(image_budget) != 1 or type(image_budget[0]) is not int
        ):
            raise ValueError("image_budget must be a one-item integer list")
        remaining = (
            max(0, min(_AI_IMAGE_MAX_COUNT, image_budget[0]))
            if image_budget is not None else _AI_IMAGE_MAX_COUNT
        )
        hydrated: list[dict[str, object]] = []
        for raw in entries:
            entry = dict(raw)
            safe_attachments: list[dict[str, object]] = []
            attachments = raw.get("attachments")
            if isinstance(attachments, (list, tuple)):
                for item in attachments[:8]:
                    if not isinstance(item, dict):
                        continue
                    safe = {
                        key: item[key]
                        for key in ("kind", "content_type", "mime_type", "size", "transcript")
                        if item.get(key) is not None
                    }
                    mime = str(
                        item.get("content_type") or item.get("mime_type") or ""
                    ).split(";", 1)[0].strip().lower()
                    kind = str(item.get("kind") or "").strip().lower()
                    declared_image = mime in _AI_IMAGE_MIME_TYPES or kind in {
                        "image", "photo", "picture", "sticker",
                    }
                    declared_size = item.get("size")
                    if (
                        remaining > 0 and declared_image
                        and (not isinstance(declared_size, int)
                             or 0 <= declared_size <= _AI_IMAGE_MAX_BYTES)
                    ):
                        # Bound provider fetch attempts, not only successfully
                        # decoded images. Broken historic refs must not turn a
                        # summary refresh into an unbounded bridge fan-out.
                        remaining -= 1
                        if image_budget is not None:
                            image_budget[0] = remaining
                        file = self._ai_image_loader(
                            source=str(raw.get("source") or ""),
                            message_id=str(raw.get("message_id") or ""),
                            attachment=item, user_id=user_id,
                        )
                        data = getattr(file, "data", None)
                        actual_mime = str(getattr(file, "content_type", "") or "").split(
                            ";", 1,
                        )[0].strip().lower()
                        if (
                            isinstance(data, bytes) and data
                            and len(data) <= _AI_IMAGE_MAX_BYTES
                            and actual_mime in _AI_IMAGE_MIME_TYPES
                        ):
                            safe["content_type"] = actual_mime
                            safe["image_bytes"] = data
                    if declared_image and "image_bytes" not in safe:
                        # Do not claim vision support when the account-scoped
                        # adapter could not provide bounded pixels.
                        safe.pop("kind", None)
                        safe.pop("content_type", None)
                        safe.pop("mime_type", None)
                    if safe:
                        safe_attachments.append(safe)
            if safe_attachments:
                entry["attachments"] = safe_attachments
            else:
                entry.pop("attachments", None)
            hydrated.append(entry)
        return hydrated

    @staticmethod
    def conversation_id(message: InboxMessage, *, user_id: str = "") -> str:
        key = f"{user_id}\0{message.conversation_key}" if user_id else message.conversation_key
        return "conv_" + hashlib.sha256(key.encode()).hexdigest()[:24]

    def receive(self, message: InboxMessage, *, route_id: str, user_id: str | None = None,
                account_ref: str = "") -> tuple[str, bool]:
        user_id = self._store.default_user_id if user_id is None else user_id
        conversation_key = message.conversation_key
        if message.source == "telegram":
            raw_ref = message.message_id.rsplit("|", 1)[-1]
            peer_id = message.peer_id or raw_ref.split(":", 1)[0]
            if account_ref:
                if not message.message_id.startswith(f"{account_ref}|"):
                    message = replace(message, message_id=f"{account_ref}|{message.message_id}")
                if not message.peer_id:
                    message = replace(message, peer_id=peer_id)
            if peer_id.lstrip("-").isdigit() and account_ref:
                conversation_key = f"telegram:{account_ref}:{peer_id}"
        conversation_id = self._store.conversation_id_for_key(
            conversation_key, user_id=user_id
        ) or "conv_" + hashlib.sha256(f"{user_id}\0{conversation_key}".encode()).hexdigest()[:24]
        policy = self._store.policy_for(message, fallback_route_id=route_id, user_id=user_id)
        if not self._store.route_allowed(
            user_id=user_id, source=message.source, route_id=policy.route_id
        ):
            raise ValueError("route is not assigned to user")
        accepted = self._store.ingest(
            message, conversation_id=conversation_id, policy=policy, user_id=user_id,
            conversation_key=conversation_key, account_ref=account_ref,
        )
        if accepted:
            for listener in tuple(self._inbound_listeners):
                listener(user_id, conversation_id, message)
        return conversation_id, accepted

    def _chatgpt_agent_fallback(self, *, agent_id: str, text: str, chat_ref: str) -> dict:
        """Deliver through the user's real browser when the server POST is blocked."""
        from . import agent_channel
        username = self._store.owner().username
        return agent_channel.call(
            agent_id, "gpt_send", {"text": text, "chat_ref": chat_ref},
            user=username, timeout_sec=115.0,
        )

    def add_inbound_listener(self, listener: object) -> None:
        self._inbound_listeners.append(listener)

    @staticmethod
    def _deterministic_triage_override(
        event: dict[str, object], chat: dict[str, object],
    ) -> str | None:
        reason = str(chat.get("reason") or "").casefold()
        if any(marker in reason for marker in ("vip", "важный контакт", "приоритетный контакт")):
            return "vip_chat"
        body = str(event.get("body") or "").casefold()
        safety_patterns = (
            r"\b(?:срочно|немедленно|экстренно|urgent|emergency)\b",
            r"\b(?:взлом|утечк\w*|мошеннич\w*|fraud|breach|hacked)\b",
            r"\b(?:суд|полици\w*|скорую|пожар|угроз\w*)\b",
            r"\b(?:оплат\w*|плат[её]ж\w*|долг\w*)\b.{0,80}\b(?:сегодня|до конца дня|просроч\w*)\b",
        )
        if any(re.search(pattern, body, re.IGNORECASE) for pattern in safety_patterns):
            return "deterministic_safety_signal"
        return None

    def _triage_actor_context(self, event: dict[str, object], user_id: str) -> dict[str, str] | None:
        """Resolve a direct peer only from this user's registered account IDs."""
        if (event.get("source") != "telegram" or event.get("direction") != "incoming"
                or event.get("conversation_kind") != "direct"):
            return None
        peer_id = str(event.get("peer_id") or "")
        receiver_id = str(event.get("account_ref") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", peer_id):
            return None
        sender_id = f"telegram:{peer_id}"
        if sender_id == receiver_id:
            return None
        accounts = {str(item["id"]): item for item in self._store.accounts(user_id=user_id)
                    if item.get("provider") == "telegram"}
        sender = accounts.get(sender_id)
        receiver = accounts.get(receiver_id)
        if sender is None or receiver is None:
            return None
        return {
            "sender_account_id": sender_id,
            "sender_display_name": str(sender["display_name"])[:320],
            "receiver_account_id": receiver_id,
            "receiver_display_name": str(receiver["display_name"])[:320],
        }

    def _cached_summary_and_refresh(
        self, *, conversation_id: str, current_message_id: str,
        current_message_source: str = "",
        generator: object, user_id: str,
    ) -> str:
        """Refresh the cache and return the newest summary for this triage.

        Summarization is an optimization, never a prerequisite for receiving or
        triaging a message.  A missing/failed summarizer therefore leaves the
        durable cursor untouched so a later event can retry the same increment.
        """
        work = self._store.conversation_summary_work(
            conversation_id, through_message_id=current_message_id, user_id=user_id,
            through_message_source=current_message_source,
        )
        if work is None:
            return ""
        previous = str(work.get("previous_summary") or "")
        messages = work.get("new_messages")
        summarize = getattr(generator, "summarize_conversation", None)
        if not callable(summarize) or not isinstance(messages, list) or not messages:
            return previous
        model_messages = self._hydrate_ai_images(messages, user_id=user_id)
        try:
            updated = summarize(
                conversation_id=conversation_id,
                previous_summary=previous,
                new_messages=model_messages,
                token_budget=int(work["summary_token_budget"]),
            )
            if not isinstance(updated, str) or not updated.strip():
                raise ValueError("conversation summarizer returned an empty summary")
            last = messages[-1]
            saved = self._store.save_conversation_summary(
                conversation_id,
                summary=updated,
                expected_through_message_rowid=int(work["expected_through_message_rowid"]),
                through_message_rowid=int(last["message_rowid"]),
                through_message_id=str(last.get("message_id") or ""),
                summarized_message_count=(
                    int(work["summarized_message_count"]) + len(messages)
                ),
                expected_messages=messages,
                user_id=user_id,
            )
            if saved:
                persisted = self._store.conversation_summary(
                    conversation_id, through_message_id=current_message_id,
                    through_message_source=current_message_source, user_id=user_id,
                )
                return str((persisted or {}).get("summary") or "")
            concurrent = self._store.conversation_summary(
                conversation_id, through_message_id=current_message_id,
                through_message_source=current_message_source,
                user_id=user_id,
            )
            if concurrent is not None:
                return str(concurrent.get("summary") or previous)
            return ""
        except Exception as error:
            _LOG.warning(
                "conversation summary refresh failed for %s: %s",
                conversation_id, error,
            )
        return previous

    def triage_workspace_event(
        self, *, event_seq: int, request_id: str, max_drafts: int = 2,
        user_id: str | None = None,
    ) -> dict[str, object]:
        if type(max_drafts) is not int or not 0 <= max_drafts <= 2:
            raise ValueError("max_drafts must be between 0 and 2")
        resolved_user = self._store._user(user_id)
        current, should_run = self._store.begin_workspace_triage(
            event_seq=event_seq, request_id=request_id, user_id=resolved_user,
        )
        if not should_run:
            if current["status"] == "running":
                return {
                    "event_seq": event_seq, "request_id": request_id,
                    "status": "pending", "retryable": True, "drafts": [],
                }
            return current
        event = self._store.workspace_event(event_seq, user_id=resolved_user)
        chat = self._store.evaluate_workspace_event(
            conversation_id=str(event["conversation_id"]), source=str(event["source"]),
            message_id=str(event["message_id"]), user_id=resolved_user,
        )
        settings = self._store.workspace_triage_settings(user_id=resolved_user)
        override = self._deterministic_triage_override(event, chat)
        if not chat["allowed"]:
            result = {
                "decision": "silent", "importance": 0.0, "urgency": "none", "confidence": 1.0,
                "reason_codes": ["workspace_policy_denied"],
                "reason_ru": "Чат отключён текущей политикой обработки.",
                "action_required": False, "action_summary": "", "deadline_at": None,
                "suggested_replies": [], "safety_override": False,
                "policy_version": settings["policy_version"],
            }
            return self._store.complete_workspace_triage(
                event_seq=event_seq, request_id=request_id, result=result,
                draft_bodies=[], user_id=resolved_user,
            )
        generator = self._generator_for(resolved_user)
        if not callable(getattr(generator, "triage_with_context", None)):
            # Per-user BYOK currently owns opt-in draft generation only. Inbox
            # triage and cache summaries stay on the deployment-owned adaptive
            # agent so every user gets the same bounded tool loop.
            generator = self._generator
        context_settings = self._store.context_settings(user_id=resolved_user)
        cached_summary = self._cached_summary_and_refresh(
            conversation_id=str(event["conversation_id"]), generator=generator,
            current_message_id=str(event["message_id"]), user_id=resolved_user,
            current_message_source=str(event["source"]),
        )
        history = self._store.bounded_conversation_context(
            str(event["conversation_id"]),
            current_message_id=str(event["message_id"]),
            current_message_source=str(event["source"]),
            user_id=resolved_user,
        )
        triage_image_budget = [_AI_IMAGE_MAX_COUNT]
        # The current message owns the first slots in the bounded hydration
        # budget. Older images may add context, but must never crowd out the
        # pixels that caused this triage operation.
        latest_attachments = self._hydrate_ai_images([{
            "source": str(event["source"]),
            "message_id": str(event["message_id"]),
            "attachments": self._store.attachments_for_message(
                source=str(event["source"]), message_id=str(event["message_id"]),
                user_id=resolved_user,
            ),
        }], user_id=resolved_user, image_budget=triage_image_budget)[0].get("attachments")
        history = self._hydrate_ai_images(
            history, user_id=resolved_user, image_budget=triage_image_budget,
        )
        message = InboxMessage(
            source=str(event["source"]), message_id=str(event["message_id"]),
            sender=str(event["sender"]), body=str(event["body"]),
            received_at=float(event["received_at"]), direction=str(event["direction"]),
            conversation_kind=str(event["conversation_kind"]), peer_id=str(event["peer_id"]),
            sender_is_bot=bool(event["sender_is_bot"]),
            attachments=tuple(latest_attachments if isinstance(latest_attachments, list) else ()),
        )
        anchor_conversation_id = str(event["conversation_id"])
        anchor_message_id = str(event["message_id"])

        def _older_context(before_message_id: str) -> list[dict[str, object]]:
            anchor = before_message_id.strip() or anchor_message_id
            if not anchor:
                return []
            page = self._store.bounded_conversation_context(
                anchor_conversation_id, before_message_id=anchor, user_id=resolved_user,
                current_message_source=str(event["source"]) if anchor == anchor_message_id else "",
                message_limit=int(context_settings["max_message_count"]),
                token_limit=int(context_settings["max_token_budget"]),
            )
            return self._hydrate_ai_images(
                page, user_id=resolved_user, image_budget=triage_image_budget,
            )

        triage = getattr(generator, "triage_with_context", None)
        try:
            if not callable(triage):
                raise RuntimeError("configured AI generator does not support importance triage")
            generated = triage(
                conversation_id=anchor_conversation_id, latest_message=message,
                history=history, max_drafts=max_drafts,
                actor_context=self._triage_actor_context(event, resolved_user),
                history_reader=_older_context,
                cached_summary=cached_summary,
                initial_context_messages=int(context_settings["message_count"]),
                max_context_messages=int(context_settings["max_message_count"]),
                max_context_token_budget=int(context_settings["max_token_budget"]),
            )
            if not isinstance(generated, dict):
                raise ValueError("AI triage result must be an object")
            importance = float(generated["importance"])
            confidence = float(generated["confidence"])
            if not 0 <= importance <= 1 or not 0 <= confidence <= 1:
                raise ValueError("AI triage scores must be between 0 and 1")
            if not settings["enabled"]:
                decision = "notify"
            elif override:
                decision = "notify"
            elif confidence < float(settings["min_confidence"]):
                decision = "review"
            elif importance >= float(settings["threshold"]):
                decision = "notify"
            else:
                decision = "silent"
            replies = generated.get("suggested_replies")
            if not isinstance(replies, list):
                raise ValueError("AI triage suggested_replies must be an array")
            draft_bodies = [
                str(item.get("body") or "").strip()
                for item in replies[:max_drafts] if isinstance(item, dict)
                and str(item.get("body") or "").strip()
            ]
            if any(len(body) > 2000 for body in draft_bodies):
                raise ValueError("AI triage draft exceeds limit")
            if decision == "silent":
                draft_bodies = []
            codes = generated.get("reason_codes")
            if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
                raise ValueError("AI triage reason_codes must be strings")
            result = {
                "decision": decision, "importance": importance,
                "urgency": str(generated.get("urgency") or "none"), "confidence": confidence,
                "reason_codes": (
                    (["triage_disabled"] if not settings["enabled"] else [])
                    + ([override] if override else []) + [str(code)[:64] for code in codes[:8]]
                )[:8],
                "reason_ru": str(generated.get("reason_ru") or "")[:500],
                "action_required": bool(generated.get("action_required")),
                "action_summary": str(generated.get("action_summary") or "")[:500],
                "deadline_at": generated.get("deadline_at"),
                "suggested_replies": [{"body": body} for body in draft_bodies],
                "safety_override": bool(override),
                "policy_version": settings["policy_version"],
            }
        except Exception as error:
            if override:
                result = {
                    "decision": "notify", "importance": 1.0, "urgency": "high", "confidence": 1.0,
                    "reason_codes": [override],
                    "reason_ru": "Сработало локальное правило безопасности или важного контакта.",
                    "action_required": True, "action_summary": "Проверить сообщение вручную.",
                    "deadline_at": None, "suggested_replies": [], "safety_override": True,
                    "policy_version": settings["policy_version"],
                }
                return self._store.complete_workspace_triage(
                    event_seq=event_seq, request_id=request_id, result=result,
                    draft_bodies=[], user_id=resolved_user,
                )
            return self._store.fail_workspace_triage(
                event_seq=event_seq, request_id=request_id,
                error=(
                    f"{type(error).__name__}: "
                    f"{str(error).strip()[:280] or 'triage generation failed'}"
                ), user_id=resolved_user,
            )
        return self._store.complete_workspace_triage(
            event_seq=event_seq, request_id=request_id, result=result,
            draft_bodies=draft_bodies, user_id=resolved_user,
        )

    def send_workspace_triage(
        self, *, event_seq: int, request_id: str, draft_id: str, actor: str,
        confirm: bool, user_id: str | None = None,
    ) -> dict[str, object]:
        if confirm is not True:
            raise ValueError("exact_confirmation_required")
        resolved_user = self._store._user(user_id)
        event = self._store.workspace_event(event_seq, user_id=resolved_user)
        triage = self._store.workspace_triage(
            event_seq=event_seq, request_id=request_id, user_id=resolved_user,
        )
        selected = str(triage.get("selected_draft_id") or "")
        if selected and selected != draft_id:
            raise ValueError("triage_choice_conflict")
        if triage.get("send_state") == "sent":
            return {
                "event_seq": event_seq, "request_id": request_id, "sent": True,
                "draft_id": draft_id, "receipt": triage.get("receipt"), "idempotent": True,
            }
        if triage.get("send_state") == "sending":
            raise ValueError("delivery_outcome_uncertain")
        policy = self._store.evaluate_workspace_event(
            conversation_id=str(event["conversation_id"]), source=str(event["source"]),
            message_id=str(event["message_id"]), user_id=resolved_user,
        )
        if not policy["allowed"]:
            raise PermissionError("workspace policy no longer allows this chat")
        drafts = {str(item["id"]): item for item in triage.get("drafts", [])}
        if draft_id not in drafts:
            raise ValueError("triage_draft_mismatch")
        snapshot = {
            "expected_text": str(drafts[draft_id]["body"]),
            "expected_chat_id": str(event["conversation_id"]),
            "expected_attachments": [],
        }
        draft = self._store.claim_draft_send(
            draft_id, user_id=resolved_user, expected_snapshot=snapshot,
        )
        try:
            send = self._prepare_claimed_draft(draft, user_id=resolved_user)
        except Exception:
            self._store.release_draft_send(draft_id, user_id=resolved_user)
            raise
        try:
            self._store.claim_workspace_triage_send(
                event_seq=event_seq, request_id=request_id, draft_id=draft_id,
                actor=actor, user_id=resolved_user,
            )
        except Exception:
            self._store.release_draft_send(draft_id, user_id=resolved_user)
            raise
        receipt = send()
        approved = self._store.approve(draft_id, receipt, user_id=resolved_user)
        self._store.complete_workspace_triage_send(
            event_seq=event_seq, request_id=request_id, draft_id=draft_id,
            receipt=receipt, user_id=resolved_user,
        )
        return {
            "event_seq": event_seq, "request_id": request_id, "sent": True,
            "draft_id": approved.id, "receipt": approved.receipt, "idempotent": False,
        }

    def react_to_message(
        self, *, conversation_id: str, message_id: str, emoji: str,
        confirm: bool, user_id: str | None = None,
    ) -> dict[str, object]:
        if confirm is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        emoji = str(emoji or "").strip()
        message_id = str(message_id or "").strip()
        if not message_id:
            return {"ok": False, "error": "message_id_required"}
        if not emoji or len(emoji) > 16:
            return {"ok": False, "error": "invalid_emoji"}
        resolved_user = self._store._user(user_id)
        conversation = self._store.conversation(conversation_id, user_id=resolved_user)
        if conversation is None:
            return {"ok": False, "error": "conversation_not_found"}
        source = str(conversation.get("source") or "")
        outbox_react = (
            getattr(self.telegram_outbox, "react", None)
            if self.telegram_outbox is not None else None
        )
        if source != "telegram" or not callable(outbox_react):
            return {"ok": False, "error": "reaction_not_supported_for_source"}
        receipt = outbox_react(
            chat=str(conversation.get("sender") or ""),
            chat_id=str(conversation.get("peer_id") or ""),
            account_ref=str(conversation.get("account_ref") or ""),
            message_id=message_id, emoji=emoji,
        )
        return {"ok": True, "reacted": True, "source": source, "receipt": receipt}

    def deep_workspace_triage(
        self, *, event_seq: int, request_id: str, actor: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        resolved_user = self._store._user(user_id)
        event = self._store.workspace_event(event_seq, user_id=resolved_user)
        policy = self._store.evaluate_workspace_event(
            conversation_id=str(event["conversation_id"]), source=str(event["source"]),
            message_id=str(event["message_id"]), user_id=resolved_user,
        )
        if not policy["allowed"]:
            raise PermissionError("workspace policy no longer allows this chat")
        triage = self._store.mark_workspace_triage_deep(
            event_seq=event_seq, request_id=request_id, actor=actor, user_id=resolved_user,
        )
        message = {
            "source": event["source"], "message_id": event["message_id"],
            "sender": event["sender"], "body": event["body"],
            "received_at": event["received_at"],
        }
        return {
            "ok": True,
            "event": {key: event[key] for key in (
                "seq", "source", "message_id", "conversation_id", "account_ref", "peer_id",
            )},
            "message": message,
            "input": {
                "request_id": request_id, "actor": actor,
                "triage": triage.get("triage"),
                "cached_summary": str((self._store.conversation_summary(
                    str(event["conversation_id"]),
                    through_message_id=str(event["message_id"]), user_id=resolved_user,
                    through_message_source=str(event["source"]),
                ) or {}).get("summary") or ""),
                "recent_context": self._store.bounded_conversation_context(
                    str(event["conversation_id"]),
                    current_message_id=str(event["message_id"]),
                    current_message_source=str(event["source"]),
                    user_id=resolved_user,
                ),
            },
        }

    def feedback_workspace_triage(
        self, *, event_seq: int, request_id: str, label: str, actor: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        resolved_user = self._store._user(user_id)
        recorded = self._store.record_workspace_triage_feedback(
            event_seq=event_seq, request_id=request_id, label=label, actor=actor,
            user_id=resolved_user,
        )
        if label == "ignore_chat":
            event = self._store.workspace_event(event_seq, user_id=resolved_user)
            self._store.set_workspace_chat_rule(
                conversation_id=str(event["conversation_id"]), action="ignore",
                reason="importance triage feedback", user_id=resolved_user,
            )
        return {"ok": True, "event_seq": event_seq, "request_id": request_id,
                "feedback": recorded["feedback"]}

    def _notify_drafts_if_still_proposed(
        self, draft_ids: Sequence[str], *, user_id: str | None = None,
    ) -> None:
        notifier = self.draft_notifier
        if notifier is None:
            return
        resolved_user_id = self._store._user(user_id)
        proposed: list[ReplyDraft] = []
        for draft_id in draft_ids:
            try:
                draft = self._store.draft(str(draft_id), user_id=resolved_user_id)
            except KeyError:
                continue
            if draft.status == "proposed" and not self._store.draft_browser_notified(
                draft.id, user_id=resolved_user_id
            ):
                proposed.append(draft)
        if not proposed:
            return
        conversation = self._store.conversation(proposed[0].conversation_id, user_id=resolved_user_id)
        if conversation is None:
            return
        notify = getattr(notifier, "notify", None)
        if not callable(notify):
            return
        try:
            notify(user_id=resolved_user_id, conversation=conversation, drafts=proposed)
        except Exception as error:  # Notification failure must never lose the draft itself.
            _LOG.warning("draft approval notification failed for %s: %s", proposed[0].id, error)

    def _notify_drafts_for_approval(
        self, drafts: Sequence[ReplyDraft], *, user_id: str | None = None,
    ) -> None:
        draft_ids = [draft.id for draft in drafts if draft.status == "proposed"]
        if self.draft_notifier is None or not draft_ids:
            return
        if self.draft_notification_delay_seconds <= 0:
            self._notify_drafts_if_still_proposed(draft_ids, user_id=user_id)
            return
        timer = threading.Timer(
            self.draft_notification_delay_seconds,
            self._notify_drafts_if_still_proposed,
            args=(draft_ids,), kwargs={"user_id": user_id},
        )
        timer.daemon = True
        timer.start()

    def manual_approval_required(self, conversation: dict) -> bool:
        """True when the manual-approve lock forces explicit approve-to-send for this conversation.

        The lock is the deployment's opt-in (USERIO_SMS_MANUAL_APPROVE_ONLY): even a
        conversation whose response_mode is auto_send stays a proposed draft until a
        human explicitly approves it.
        """
        if self.sms_manual_approve and str(conversation.get("source") or "") == "sms":
            return True
        return self.max_manual_approve and str(conversation.get("source") or "") == "max"

    def receive_and_plan(
        self, message: InboxMessage, *, route_id: str, user_id: str | None = None
    ) -> tuple[str, bool, ReplyDraft | None]:
        user_id = self._store.default_user_id if user_id is None else user_id
        conversation_id, accepted = self.receive(message, route_id=route_id, user_id=user_id)
        if not accepted:
            return conversation_id, False, None
        draft = self.propose(conversation_id, message, user_id=user_id)
        conversation = self._store.conversation(conversation_id, user_id=user_id)
        if (
            conversation and conversation["response_mode"] == "auto_send"
            and not self.manual_approval_required(conversation)
        ):
            draft = self.approve(draft.id, user_id=user_id)
        else:
            self._notify_drafts_for_approval([draft], user_id=user_id)
        return conversation_id, True, draft

    def propose(
        self, conversation_id: str, message: InboxMessage, *, user_id: str | None = None
    ) -> ReplyDraft:
        return self.propose_variants(conversation_id, message, limit=1, user_id=user_id)[0]

    def propose_from_conversation(
        self, conversation_id: str, *, limit: int = 3, user_id: str | None = None
    ) -> list[ReplyDraft]:
        """Run the opt-in AI action against the latest stored inbound message."""
        conversation = self._store.conversation(conversation_id, user_id=user_id)
        if conversation is None:
            raise KeyError("conversation not found")
        messages = list(conversation["messages"])
        if not messages:
            raise ValueError("conversation has no messages")
        latest = messages[-1]
        message = InboxMessage(
            source=str(latest["source"]), message_id=str(latest["message_id"]), sender=str(latest["sender"]),
            body=str(latest["body"]), received_at=float(latest["received_at"]),
        )
        return self.propose_for_approval(
            conversation_id, message, limit=limit, user_id=user_id
        )

    def propose_for_approval(
        self, conversation_id: str, message: InboxMessage, *, limit: int = 3,
        user_id: str | None = None,
    ) -> list[ReplyDraft]:
        drafts = self.propose_variants(
            conversation_id, message, limit=limit, user_id=user_id
        )
        self._notify_drafts_for_approval(drafts, user_id=user_id)
        return drafts

    def create_manual_draft(
        self, conversation_id: str, *, body: str, user_id: str | None = None
    ) -> ReplyDraft:
        if self._store.conversation(conversation_id, user_id=user_id) is None:
            raise KeyError("conversation not found")
        text = body.strip()
        if not text:
            raise ValueError("draft body is required")
        draft = ReplyDraft("draft_" + uuid.uuid4().hex, conversation_id, text, "proposed")
        self._store.add_draft(draft, user_id=user_id)
        self._notify_drafts_for_approval([draft], user_id=user_id)
        return draft

    def _generator_for(self, user_id: str | None) -> object:
        """BYOK: a user's own endpoint/model/key wins over the server default."""
        resolved = self._store.default_user_id if user_id is None else user_id
        cached = self._user_generators.get(resolved)
        if cached is not None:
            return cached
        settings = self._store.ai_settings(user_id=resolved)
        if settings is None:
            return self._generator
        bridge_url = os.environ.get("USERIO_BYOK_BRIDGE_URL", "").strip()
        bridge_token = os.environ.get("USERIO_BYOK_BRIDGE_TOKEN", "").strip()
        if not bridge_url or not bridge_token:
            return self._generator
        from .adapters import ByokBridgeGenerator

        generator = ByokBridgeGenerator(
            bridge_url=bridge_url, bridge_token=bridge_token,
            endpoint=settings["endpoint"], model=settings["model"], api_key=settings["token"],
        )
        self._user_generators[resolved] = generator
        return generator

    def propose_variants(
        self, conversation_id: str, message: InboxMessage, *, limit: int = 3,
        user_id: str | None = None,
    ) -> list[ReplyDraft]:
        if limit < 1:
            raise ValueError("draft limit must be positive")
        if self._store.conversation(conversation_id, user_id=user_id) is None:
            raise KeyError("conversation not found")
        generator = self._generator_for(user_id)
        suggest_variants = getattr(generator, "suggest_variants", None)
        suggest_with_context = getattr(generator, "suggest_with_context", None)
        generated: Sequence[str]
        history = self._store.bounded_conversation_context(
            conversation_id, current_message_id=message.message_id,
            user_id=user_id,
        )
        if callable(suggest_with_context):
            generated = suggest_with_context(conversation_id=conversation_id, latest_message=message, history=history, limit=limit)
        elif callable(suggest_variants):
            generated = suggest_variants(conversation_id=conversation_id, latest_message=message, limit=limit)
        else:
            generated = [generator.suggest(conversation_id=conversation_id, latest_message=message)]
        bodies = [str(body).strip() for body in generated if str(body).strip()][:limit]
        if not bodies:
            raise ValueError("AI produced no reply variants")
        drafts = [ReplyDraft("draft_" + uuid.uuid4().hex, conversation_id, body, "proposed") for body in bodies]
        for draft in drafts:
            self._store.add_draft(draft, user_id=user_id)
        return drafts

    def approve(self, draft_id: str, *, user_id: str | None = None,
                expected_snapshot: dict[str, object] | None = None) -> ReplyDraft:
        resolved_user_id = self._store._user(user_id)
        if not self._store.send_enabled(user_id=resolved_user_id):
            raise DeliveryUnavailableError("outbound delivery is disabled by user policy")
        draft = self._store.claim_draft_send(
            draft_id, user_id=resolved_user_id, expected_snapshot=expected_snapshot,
        )
        if draft.status == "approved":
            return draft
        try:
            send = self._prepare_claimed_draft(draft, user_id=resolved_user_id)
        except Exception:
            self._store.release_draft_send(draft_id, user_id=resolved_user_id)
            raise
        # Once a provider may have accepted delivery, a timeout or receipt-store
        # failure is uncertain. Keep the durable claim; never retry blindly.
        receipt = send()
        return self._store.approve(draft_id, receipt, user_id=resolved_user_id)

    def _prepare_claimed_draft(self, draft: ReplyDraft, *, user_id: str):
        conversation = self._store.conversation(draft.conversation_id, user_id=user_id)
        if conversation is None:
            raise KeyError("conversation not found")
        user_id = self._store.default_user_id if user_id is None else user_id
        if not self._store.route_allowed(
            user_id=user_id, source=str(conversation["source"]), route_id=str(conversation["route_id"])
        ):
            raise ValueError("route is not assigned to user")
        source = str(conversation["source"])
        if source == "gmail" or source.startswith("gmail:"):
            if self.gmail_outbox is None:
                raise DeliveryUnavailableError("Gmail delivery is not configured")
            account_alias = "gmail" if source == "gmail" else source.partition(":")[2]
            account_ref = str(conversation.get("account_ref") or "")
            if account_ref and account_ref != account_alias:
                raise DeliveryUnavailableError("Gmail conversation account does not match source")
            account = next((item for item in self._store.accounts(user_id=user_id)
                            if item["provider"] == "gmail"
                            and item["credential_ref"] == f"himalaya:{account_alias}"
                            and item["enabled"] and "reply" in item["capabilities"]), None)
            if account is None:
                raise DeliveryUnavailableError("Gmail account is not configured")
            latest = list(conversation["messages"])[-1]
            send = partial(self.gmail_outbox.send_reply,
                account=account_alias, sender=str(account["display_name"]), recipient=str(conversation["sender"]),
                message_id=str(latest["message_id"]), body=draft.body, draft_id=draft.id,
            )
        elif str(conversation["source"]).startswith("chatgpt"):
            # chatgpt:<slug> sources carry the account; account_ref is the fallback.
            account_ref = str(conversation["source"]).partition(":")[2] or str(conversation.get("account_ref") or "")
            if self.chatgpt_outbox is not None:
                principal = self._store.user(user_id)
                if principal is None:
                    raise ValueError(f"unknown UserIO user: {user_id}")
                send = partial(self.chatgpt_outbox.send_reply,
                    chat_ref=str(conversation["sender"]), draft_id=draft.id, body=draft.body,
                    account_ref=account_ref, user=principal.username,
                    agent_fallback=self._chatgpt_agent_fallback,
                )
            else:
                send_chatgpt_reply = getattr(self._outbox, "send_chatgpt_reply", None)
                if not callable(send_chatgpt_reply):
                    raise ValueError("configured outbox does not support ChatGPT delivery")
                send = partial(send_chatgpt_reply,
                    chat_ref=str(conversation["sender"]), draft_id=draft.id, body=draft.body
                )
        elif conversation["source"] == "sms":
            if self.sms_gateway is None or user_id != self.sms_user_id:
                raise ValueError("Android SMS adapter is not configured for this UserIO user")
            send = partial(self.sms_gateway.send, to=str(conversation["sender"]), body=draft.body)
        elif conversation["source"] == "max":
            if self.max_client is None or user_id != self.max_user_id:
                raise ValueError("MAX adapter is not configured for this UserIO user")
            chat_id = str(conversation.get("peer_id") or conversation.get("sender") or "")

            def _send_max(client=self.max_client, chat_id=chat_id, body=draft.body) -> str:
                receipt = client.send(chat_id=chat_id, text=body)
                if isinstance(receipt, dict):
                    return str(receipt.get("message_id") or receipt.get("receipt") or "max-sent")
                return str(receipt)

            send = _send_max
        elif conversation["source"] == "telegram" and self.telegram_outbox is not None:
            messages = list(conversation["messages"])
            chat_id = str(conversation.get("peer_id") or "")
            send = partial(self.telegram_outbox.send_reply,
                chat=str(conversation["sender"]), chat_id=chat_id, body=draft.body, draft_id=draft.id,
                account_ref=str(conversation.get("account_ref") or ""),
            )
        elif conversation["source"] == "whatsapp" and self.whatsapp_outbox is not None:
            send = partial(self.whatsapp_outbox.send,
                chat_id=str(conversation["sender"]), text=draft.body
            )
        else:
            send = partial(self._outbox.send_reply,
                route_id=str(conversation["route_id"]), conversation_id=draft.conversation_id,
                draft_id=draft.id, body=draft.body,
            )
        return send
