"""Execution of capability-gated UserIO MCP tools."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from .adapters import AdapterNotSupported, UnifiedChannels
from .contracts import InboxMessage, UserPrincipal
from .token_permissions import READ_ONLY_TOOLS, MEETING_RECORDING_TOOLS
from .mcp_catalog import TOOL_CAPABILITIES
from .service import UserIOService
from .store import SQLiteUserIOStore


_WORKSPACE_EVENT_BODY_LIMIT = 4096


class UserIOToolDispatcher:
    """Run one authenticated tool without owning transport or manifest logic."""

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService) -> None:
        self._store = store
        self._service = service

    def _attach_task_reply_association(
        self, message: object, *, conversation_id: str, user_id: str,
    ) -> None:
        if not isinstance(message, dict):
            return
        if str(message.get("direction") or "incoming") != "incoming":
            return
        message_id = str(message.get("message_id") or "")
        if not message_id:
            return
        association = self._service.task_reply_association_for_message(
            conversation_id=conversation_id, message_id=message_id, user_id=user_id,
        )
        if association is not None:
            message["task_reply_association"] = association

    def _attach_task_reply_associations(
        self, messages: object, *, conversation_id: str, user_id: str,
    ) -> None:
        if not isinstance(messages, list):
            return
        for message in messages:
            self._attach_task_reply_association(
                message, conversation_id=conversation_id, user_id=user_id,
            )

    def dispatch(
        self, name: str, arguments: dict[str, Any], *, principal: UserPrincipal
    ) -> dict[str, Any]:
        if principal.messenger_grants is not None:
            from .scoped_permissions import enforce, dispatch_scoped
            try:
                enforce(self._store, principal, name, arguments)
                if name == 'userio.accounts.list':
                    return {'ok':True, 'accounts':[{k:v for k,v in a.items() if k!='credential_ref'}
                        for a in self._store.accounts(user_id=principal.user_id) if a['id'] in principal.messenger_accounts]}
                if name.startswith('userio.messenger.'):
                    return dispatch_scoped(self._store,self._service,principal,name,arguments)
                if name.startswith('userio.channels.'):
                    return dispatch_scoped(self._store,self._service,principal,name,arguments)
            except (PermissionError,ValueError,KeyError) as error:
                return {'ok':False,'error':str(error).strip("'")}
        if principal.meeting_recorder and name not in MEETING_RECORDING_TOOLS:
            return {'ok': False, 'error': 'meeting_recording_scope_only'}
        if principal.messenger_accounts is not None and not name.startswith(('userio.messenger.','userio.calls.telegram.')):
            return {'ok':False,'error':'messenger_scope_only'}
        if principal.read_only and name not in READ_ONLY_TOOLS:
            return {"ok": False, "error": "read_only_token"}
        user_id = principal.user_id
        if name.startswith('userio.messenger.'):
            from .messenger import dispatch
            return dispatch(self._store, self._service, principal, name, arguments)
        channels = UnifiedChannels(self._store, self._service, user_id)
        try:
            if name.startswith('userio.diagnostic.'):
                from .diagnostic_chats import dispatch
                return dispatch(self._store, principal, name, arguments)
            required_capability = TOOL_CAPABILITIES.get(name)
            if required_capability and not self._store.capability_enabled(
                required_capability, user_id=user_id
            ):
                return {
                    "ok": False,
                    "error": f"{required_capability}_capability_disabled",
                }
            if name == "userio.channels.search":
                ignored_chats = self._ignored_chats(arguments)
                from .search_surface import search_tool_response
                return search_tool_response(
                    self._store, arguments, user_id=user_id,
                    source=self._optional(arguments, "channel"), ignored=ignored_chats,
                )
            if name == "userio.channels.export":
                from .chat_export import export_conversation
                conversation_id = str(arguments.get("conversation_id") or "").strip()
                if not conversation_id:
                    raise ValueError("conversation_id is required")
                result = export_conversation(
                    self._store, conversation_id, user_id=user_id,
                    include_images=bool(arguments.get("include_images")),
                    since=arguments.get("since"), until=arguments.get("until"),
                    max_messages=int(arguments.get("max_messages") or 20000),
                )
                if not result:
                    return {"ok": False, "error": "conversation not found"}
                markdown = str(result.pop("markdown"))
                result["preview"] = markdown[:2000]
                if len(markdown.encode("utf-8")) <= 32768:
                    result["content"] = markdown
                return result
            if name == "userio.channels.list":
                ignored_chats = self._ignored_chats(arguments)
                query = str(arguments.get("search") or "").strip()
                if query:
                    from .search_surface import search_response
                    return search_response(
                        self._store, query, arguments, user_id=user_id,
                        source=self._optional(arguments, "channel"), ignored=ignored_chats,
                    )
                adapter = channels.adapter(self._optional(arguments, "channel"))
                chats = adapter.list(limit=int(arguments.get("limit", 100)))
                return {
                    "ok": True,
                    "chats": [
                        chat
                        for chat in chats
                        if str(chat["id"]) not in ignored_chats
                    ],
                }
            if name == "userio.channels.read":
                ignored_chats = self._ignored_chats(arguments)
                adapter = channels.adapter(self._optional(arguments, "channel"))
                before_id = arguments.get("before_id")
                history_limit = arguments.get("history_limit")
                if (self._optional(arguments, "channel") == "max"
                        and before_id is None and history_limit is not None):
                    if type(history_limit) is not int or not 1 <= history_limit <= 100:
                        raise ValueError("history_limit must be between 1 and 100")
                    result = adapter.read(
                        chat_id=self._optional(arguments, "chat_id"),
                        message_id=self._optional(arguments, "message_id"),
                    )
                    if "chat" in result:
                        result["chat"]["messages"] = result["chat"].get("messages", [])[-history_limit:]
                elif before_id is not None or history_limit is not None:
                    if self._optional(arguments, "channel") != "telegram":
                        raise ValueError("history pagination requires channel=telegram")
                    result = adapter.read(
                        chat_id=self._optional(arguments, "chat_id"),
                        message_id=self._optional(arguments, "message_id"),
                        before_id=before_id,
                        history_limit=50 if history_limit is None else history_limit,
                    )
                    if before_id is None and history_limit is not None and "chat" in result:
                        result["chat"]["messages"] = result["chat"].get("messages", [])[-history_limit:]
                else:
                    result = adapter.read(
                        chat_id=self._optional(arguments, "chat_id"),
                        message_id=self._optional(arguments, "message_id"),
                    )
                record = result.get("chat") or result.get("message") or {}
                conversation_id = str(
                    record.get("id") or record.get("conversation_id") or ""
                )
                if conversation_id in ignored_chats:
                    raise KeyError("chat not found")
                if conversation_id:
                    self._attach_task_reply_associations(
                        record.get("messages"),
                        conversation_id=conversation_id, user_id=user_id,
                    )
                    self._attach_task_reply_association(
                        result.get("message"),
                        conversation_id=conversation_id, user_id=user_id,
                    )
                return {"ok": True, **result}
            if name.startswith('userio.meetings.telemost.'):
                from .telemost.meetings import meetings_for
                meetings=meetings_for(self._service)
                if name.endswith('.start'):record=meetings.start(user_id=user_id,**arguments)
                elif name.endswith('.get'):record=meetings.get(self._required(arguments,'meeting_id'),user_id)
                elif name.endswith('.stop'):record=meetings.stop(self._required(arguments,'meeting_id'),user_id)
                else:raise KeyError('unknown meeting operation')
                return {'ok':True,'meeting':record}
            if name.startswith('userio.calls.matrix.'):
                from .matrix_calls import calls_for
                calls = calls_for(self._service)
                if name == 'userio.calls.matrix.start':
                    call = calls.start(user_id=user_id, **arguments)
                elif name == 'userio.calls.matrix.get':
                    call = calls.get(self._required(arguments,'call_id'),user_id)
                elif name == 'userio.calls.matrix.stop':
                    call = calls.stop(self._required(arguments,'call_id'),user_id)
                else: raise KeyError('unknown call operation')
                return {'ok':True,'call':call}
            if name.startswith('userio.calls.telegram.'):
                from .telegram_calls import calls_for
                calls = calls_for(self._service)
                if principal.messenger_accounts is not None:
                    account_ref=(arguments.get('account_ref') if name=='userio.calls.telegram.start'
                        else calls.get(self._required(arguments,'call_id'),user_id)['account_ref'])
                    if account_ref not in principal.messenger_accounts:
                        raise PermissionError('account_scope_denied')
                if name == 'userio.calls.telegram.start':
                    call = calls.start(user_id=user_id, **arguments)
                elif name == 'userio.calls.telegram.get':
                    call = calls.get(self._required(arguments,'call_id'),user_id)
                elif name == 'userio.calls.telegram.stop':
                    call = calls.stop(self._required(arguments,'call_id'),user_id)
                else: raise KeyError('unknown call operation')
                return {'ok':True,'call':call}
            if name == "userio.channels.download":
                file_ref=self._required(arguments,"file_ref")
                attachment_index=arguments.get('attachment_index',0)
                if type(attachment_index) is not int or not 0<=attachment_index<8:
                    raise ValueError('invalid_attachment_index')
                mode=arguments.get('mode','base64')
                if mode=='mcp_resource':
                    include_images=arguments.get('include_images',True)
                    if type(include_images) is not bool: raise ValueError('include_images_must_be_boolean')
                    channel,separator,provider_ref=file_ref.partition(':')
                    if not separator or channel not in {'telegram','max'}:
                        raise ValueError('mcp_resource_mode_requires_telegram_or_max')
                    from .mcp_files import MAX_MCP_FILE_BYTES, mcp_attachment
                    if channel=='telegram':
                        download_args={'file_ref':provider_ref,'max_bytes':MAX_MCP_FILE_BYTES}
                        if attachment_index:
                            download_args['attachment_index']=attachment_index
                        file=channels.adapter('telegram').download_for_ai(**download_args)
                    else:
                        file=channels.download(file_ref=file_ref,attachment_index=attachment_index,
                                               max_bytes=MAX_MCP_FILE_BYTES)
                    metadata,blocks=mcp_attachment(file,include_images=include_images)
                    return {'ok':True,'file':metadata,'_mcp_content_blocks':blocks}
                if mode!='base64': raise ValueError('invalid_download_mode')
                from .mcp_files import MAX_MCP_FILE_BYTES
                channel,_,provider_ref=file_ref.partition(':')
                if channel=='telegram':
                    download_args={'file_ref':provider_ref,'max_bytes':MAX_MCP_FILE_BYTES}
                    if attachment_index:
                        download_args['attachment_index']=attachment_index
                    file=channels.adapter('telegram').download_for_ai(**download_args)
                else:
                    file = channels.download(file_ref=file_ref,attachment_index=attachment_index,
                                             max_bytes=MAX_MCP_FILE_BYTES)
                return {
                    "ok": True,
                    "file": {
                        "filename": file.filename,
                        "content_type": file.content_type,
                        "size": len(file.data),
                        "sha256": hashlib.sha256(file.data).hexdigest(),
                        "encoding": "base64",
                        "data": base64.b64encode(file.data).decode(),
                    },
                }
            if name == "userio.channels.send_draft":
                attachments = arguments.get("attachments")
                if attachments is not None and not isinstance(attachments, list):
                    raise ValueError("attachments must be an array")
                draft = channels.send(
                    chat_id=self._required(arguments, "chat_id"),
                    text=self._required(arguments, "text"),
                    attachments=attachments,
                )
                conversation = self._store.conversation(
                    draft.conversation_id, user_id=user_id
                ) or {}
                manual_lock = self._service.manual_approval_required(conversation)
                hint = (
                    "Автоматическая отправка отключена. Чтобы отправить сообщение, "
                    "подтвердите этот черновик."
                    if manual_lock else
                    "Черновик сохранён. Чтобы отправить сообщение, "
                    "подтвердите этот текст и получателя."
                )
                return {
                    "ok": True,
                    "draft": self._draft(draft),
                    "sent": False,
                    "approval_required": True,
                    "channel": str(conversation.get("source") or ""),
                    "response_mode": str(conversation.get("response_mode") or ""),
                    "manual_approval_only": manual_lock,
                    "approval_hint": hint,
                }
            if name == "userio.workspace.poll":
                page = self._store.workspace_events(
                    after=arguments.get("after", 0),
                    limit=arguments.get("limit", 50),
                    user_id=user_id,
                )
                events = [self._workspace_event(event) for event in page["events"]]
                return {
                    "ok": True,
                    "schema": "universal.workspace-events.v1",
                    **page,
                    "events": events,
                }
            if name == "userio.workspace.claim":
                claimed = self._store.claim_workspace_event(
                    worker_id=self._required(arguments, "worker_id"),
                    lease_seconds=arguments.get("lease_seconds", 600),
                    after=arguments.get("after", 0),
                    telegram_direct_only=arguments.get("telegram_direct_only", False),
                        automatic=True,
                    user_id=user_id,
                )
                if claimed is None:
                    return {"ok": True, "claimed": False}
                claimed["event"] = self._workspace_event(claimed["event"])
                return {"ok": True, "claimed": True, **claimed}
            if name == "userio.workspace.renew":
                claim = self._store.renew_workspace_claim(
                    event_seq=arguments.get("event_seq"),
                    worker_id=self._required(arguments, "worker_id"),
                    lease_token=self._required(arguments, "lease_token"),
                    lease_seconds=arguments.get("lease_seconds", 600),
                    user_id=user_id,
                )
                return {"ok": True, "claim": claim}
            if name in {"userio.workspace.complete", "userio.workspace.fail"}:
                transition = (
                    self._store.complete_workspace_claim
                    if name.endswith("complete") else self._store.fail_workspace_claim
                )
                claim = transition(
                    event_seq=arguments.get("event_seq"),
                    worker_id=self._required(arguments, "worker_id"),
                    lease_token=self._required(arguments, "lease_token"),
                    detail=self._optional(arguments, "detail"),
                    user_id=user_id,
                )
                return {"ok": True, "claim": claim}
            if name == "userio.workspace.claim_log":
                return {
                    "ok": True,
                    **self._store.workspace_claim_log(
                        event_seq=arguments.get("event_seq"), user_id=user_id,
                    ),
                }
            if name == "userio.workspace.exclusions.list":
                return {
                    "ok": True,
                    "exclusions": self._store.workspace_exclusions(user_id=user_id),
                }
            if name == "userio.workspace.exclusions.add":
                exclusion = self._store.add_workspace_exclusion(
                    conversation_id=self._required(arguments, "conversation_id"),
                    reason=self._optional(arguments, "reason") or "",
                    user_id=user_id,
                )
                return {"ok": True, "exclusion": exclusion}
            if name == "userio.workspace.exclusions.remove":
                return {
                    "ok": True,
                    "removed": self._store.remove_workspace_exclusion(
                        conversation_id=self._required(arguments, "conversation_id"),
                        user_id=user_id,
                    ),
                }
            if name == "userio.workspace.policy.get":
                return {"ok": True, **self._store.workspace_policy(user_id=user_id)}
            if name == "userio.workspace.policy.set_default":
                return {"ok": True, **self._store.set_workspace_default(
                    conversation_kind=self._required(arguments, "conversation_kind"),
                    enabled=arguments.get("enabled"), user_id=user_id,
                )}
            if name == "userio.workspace.policy.telegram_bots.set":
                return {"ok": True, **self._store.set_workspace_default(
                    conversation_kind="telegram_bot",
                    enabled=arguments.get("enabled"), user_id=user_id,
                )}
            if name == "userio.workspace.policy.chats.list":
                return {"ok": True, "chats": self._store.workspace_chat_rules(
                    user_id=user_id, source=arguments.get("source", ""),
                    account_ref=arguments.get("account_ref", ""),
                    peer_id=arguments.get("peer_id", ""),
                    conversation_kind=arguments.get("conversation_kind", ""),
                    action=arguments.get("action", ""), query=arguments.get("query", ""),
                    limit=arguments.get("limit", 100), offset=arguments.get("offset", 0)),
                        "revision": self._store.workspace_policy(user_id=user_id)["revision"]}
            if name == "userio.workspace.policy.chats.set":
                return {"ok": True, "chat": self._store.set_workspace_chat_rule(
                    conversation_id=self._required(arguments, "conversation_id"),
                    action=self._required(arguments, "action"),
                    reason=self._optional(arguments, "reason") or "", user_id=user_id,
                )}
            if name == "userio.workspace.policy.evaluate":
                return {"ok": True, "chat": self._store.evaluate_workspace_chat(
                    conversation_id=self._required(arguments, "conversation_id"), user_id=user_id,
                )}
            if name == "userio.workspace.triage.get":
                return {"ok": True, "settings": self._store.workspace_triage_settings(user_id=user_id)}
            if name == "userio.workspace.triage.set":
                return {"ok": True, "settings": self._store.set_workspace_triage_settings(
                    enabled=arguments.get("enabled"), threshold=arguments.get("threshold"),
                    min_confidence=arguments.get("min_confidence"), user_id=user_id,
                )}
            if name == "userio.workspace.triage.feedback":
                return self._service.feedback_workspace_triage(
                    event_seq=arguments.get("event_seq"),
                    request_id=self._required(arguments, "request_id"),
                    label=self._required(arguments, "label"),
                    actor=f"mcp:{principal.username}", user_id=user_id,
                )
            if name == "userio.users.create":
                if principal.role != "owner":
                    return {"ok": False, "error": "owner_required"}
                user, token = self._store.create_user(
                    self._required(arguments, "username"),
                    self._required(arguments, "password"),
                )
                return {
                    "ok": True,
                    "user": {
                        "id": user.user_id,
                        "username": user.username,
                        "role": user.role,
                    },
                    "token": token,
                    "token_returned_once": True,
                }
            if name == "userio.inbox.list_new":
                ignored_chats = self._ignored_chats(arguments)
                channel = self._optional(arguments, "channel")
                messages = self._store.new_messages(
                    source=channel, limit=int(arguments.get("limit", 50)), user_id=user_id
                )
                messages = [
                    message for message in messages
                    if str(message["conversation_id"]) not in ignored_chats
                ]
                for message in messages:
                    self._attach_task_reply_association(
                        message, conversation_id=str(message["conversation_id"]),
                        user_id=user_id,
                    )
                return {"ok": True, "messages": messages}
            if name == "userio.conversation.get":
                ignored_chats = self._ignored_chats(arguments)
                conversation_id = self._required(arguments, "conversation_id")
                if conversation_id in ignored_chats:
                    return {"ok": True, "conversation": None}
                conversation = self._store.conversation(
                    conversation_id, user_id=user_id,
                )
                if conversation is not None:
                    self._attach_task_reply_associations(
                        conversation.get("messages"),
                        conversation_id=str(conversation["id"]), user_id=user_id,
                    )
                return {"ok": True, "conversation": conversation}
            if name == "userio.message.mark_seen":
                return {
                    "ok": True,
                    "changed": self._store.mark_seen(
                        source=self._required(arguments, "source"),
                        message_id=self._required(arguments, "message_id"),
                        user_id=user_id,
                    ),
                }
            if name == "userio.draft.create":
                draft = self._service.create_manual_draft(
                    self._required(arguments, "conversation_id"),
                    body=self._required(arguments, "body"),
                    user_id=user_id,
                )
                return {"ok": True, "draft": self._draft(draft)}
            if name == "userio.draft.update":
                draft = self._store.update_draft(
                    self._required(arguments, "draft_id"),
                    body=self._required(arguments, "body"),
                    user_id=user_id,
                )
                return {"ok": True, "draft": self._draft(draft)}
            if name == "userio.draft.delete":
                return {
                    "ok": True,
                    "deleted": self._store.delete_draft(
                        self._required(arguments, "draft_id"), user_id=user_id
                    ),
                }
            if name == "userio.draft.approve_send":
                if not self._store.send_enabled(user_id=user_id):
                    return {"ok": False, "error": "outbound_delivery_disabled"}
                return self._approve(arguments, principal)
            if name == "userio.draft.reconcile_delivery":
                return self._reconcile_delivery(arguments, principal)
            if name == "userio.conversation.delete_local":
                return self._delete_conversation(arguments, principal)
            if name == "userio.accounts.list":
                accounts = self._store.accounts(user_id=user_id)
                if principal.read_only:
                    accounts = [{**a, "capabilities": [c for c in a.get("capabilities", []) if c != "reply"]} for a in accounts]
                return {"ok": True, "accounts": accounts}
            if name == "userio.ai.propose":
                return self._propose(arguments, principal)
        except PermissionError as error:
            return {"ok": False, "error": str(error)}
        except AdapterNotSupported as error:
            return {"ok": False, "error": str(error)}
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}
        except (KeyError, TypeError, ValueError) as error:
            payload = {
                "ok": False,
                "error": str(error).strip("'") or "invalid_arguments",
            }
            code = getattr(error, "code", "")
            if code:
                # A stable machine-readable reason. A consumer must branch on
                # the code, never on the localized message text.
                payload["code"] = str(code)
                payload["retry_allowed"] = False
            return payload
        return {"ok": False, "error": "unknown_tool"}

    @staticmethod
    def _snapshot(arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """The exact draft the consumer reviewed before approving it.

        The whole snapshot is mandatory. A partial snapshot cannot prove the
        draft did not change since it was reviewed, and an absent snapshot
        would approve whatever the draft says at that moment.
        """
        keys = ("expected_text", "expected_chat_id", "expected_attachments")
        if (
            not all(key in arguments for key in keys)
            or not isinstance(arguments["expected_text"], str)
            or not isinstance(arguments["expected_chat_id"], str)
            or not isinstance(arguments["expected_attachments"], list)
            or not all(
                isinstance(item, str)
                for item in arguments["expected_attachments"]
            )
        ):
            return None, "expected_text, expected_chat_id and expected_attachments are all required"
        return {key: arguments[key] for key in keys}, ""

    def _reconcile_delivery(
        self, arguments: dict[str, Any], principal: UserPrincipal
    ) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        # Ownership first, exactly as on the approve path.
        self._store.draft(
            self._required(arguments, "draft_id"), user_id=principal.user_id
        )
        snapshot, problem = self._snapshot(arguments)
        if snapshot is None:
            return {"ok": False, "error": "complete_draft_snapshot_required", "detail": problem}
        result = self._service.reconcile_draft_delivery(
            self._required(arguments, "draft_id"),
            user_id=principal.user_id,
            expected_snapshot=snapshot,
            provider_history=arguments.get("provider_history") is True,
            connector_available_since=float(arguments.get("connector_available_since") or 0.0),
        )
        return {"ok": True, **result}

    def _approve(
        self, arguments: dict[str, Any], principal: UserPrincipal
    ) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        # Ownership first: a principal that does not own the draft must not
        # learn anything about it, not even which arguments are missing.
        self._store.draft(
            self._required(arguments, "draft_id"), user_id=principal.user_id
        )
        snapshot, problem = self._snapshot(arguments)
        if snapshot is None:
            return {"ok": False, "error": "complete_draft_snapshot_required", "detail": problem}
        draft = self._service.approve(
            self._required(arguments, "draft_id"),
            user_id=principal.user_id,
            expected_snapshot=snapshot,
        )
        conversation = self._store.conversation(
            draft.conversation_id, user_id=principal.user_id
        ) or {}
        return {
            "ok": True,
            "draft": self._draft(draft),
            "sent": draft.status == "approved",
            "receipt": draft.receipt or None,
            "channel": str(conversation.get("source") or ""),
            "delivery_note": self._delivery_note(conversation),
        }

    @staticmethod
    def _delivery_note(conversation: dict[str, Any]) -> str:
        source = str(conversation.get("source") or "")
        if source == "sms":
            return "Android gateway accepted the SMS; carrier delivery is not guaranteed."
        if source.startswith("gmail:"):
            return "Sent through the Gmail account."
        if source.startswith("chatgpt"):
            return "Sent through the ChatGPT web session."
        if source == "telegram":
            return "Sent through the Telegram account."
        if source == "whatsapp":
            return "Sent through the WhatsApp bridge."
        route = conversation.get("route_id") or source or "unknown"
        return f"Sent through route {route}."

    def _delete_conversation(
        self, arguments: dict[str, Any], principal: UserPrincipal
    ) -> dict[str, Any]:
        if arguments.get("confirm") is not True:
            return {"ok": False, "error": "exact_confirmation_required"}
        deleted = self._store.delete_conversation(
            self._required(arguments, "conversation_id"),
            user_id=principal.user_id,
        )
        return {"ok": True, "deleted": deleted, "scope": "local_userio_only"}

    def _propose(
        self, arguments: dict[str, Any], principal: UserPrincipal
    ) -> dict[str, Any]:
        message = InboxMessage(
            self._required(arguments, "source"),
            self._required(arguments, "message_id"),
            self._required(arguments, "sender"),
            self._required(arguments, "body"),
            0.0,
        )
        drafts = self._service.propose_for_approval(
            self._required(arguments, "conversation_id"),
            message,
            limit=int(arguments.get("limit", 3)),
            user_id=principal.user_id,
        )
        return {"ok": True, "drafts": [self._draft(draft) for draft in drafts]}

    @staticmethod
    def _required(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value.strip()

    @staticmethod
    def _optional(arguments: dict[str, Any], key: str) -> str | None:
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        return value.strip() or None

    @staticmethod
    def _ignored_chats(arguments: dict[str, Any]) -> frozenset[str]:
        values = arguments.get("ignored_chats", [])
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError(
                "ignored_chats must be an array of at most 100 chat ids"
            )
        if any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError("ignored_chats must contain non-empty chat ids")
        return frozenset(value.strip() for value in values)

    @staticmethod
    def _workspace_event(event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        body = str(payload.get("body") or "")
        if len(body) > _WORKSPACE_EVENT_BODY_LIMIT:
            payload["body"] = body[:_WORKSPACE_EVENT_BODY_LIMIT]
            payload["body_truncated"] = True
        return payload

    @staticmethod
    def _draft(draft: Any) -> dict[str, str]:
        payload = {
            "id": draft.id,
            "conversation_id": draft.conversation_id,
            "body": draft.body,
            "status": draft.status,
        }
        receipt = getattr(draft, "receipt", "")
        if receipt:
            payload["receipt"] = receipt
        return payload
