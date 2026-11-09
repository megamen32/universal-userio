"""Regression: the ChatGPT 403 browser fallback must address the sending
user's agent queue. The queue is keyed by (user, agent_id), so addressing it
with the service owner's username hands the command to a different user's
browser whenever both registered the same legacy agent id.
"""
from __future__ import annotations

from universal_userio import adapters, agent_channel
from universal_userio.adapters import ChatGPTWebOutbox
from universal_userio.contracts import ReplyDraft
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        raise AssertionError("no AI draft may run on the fallback path")


class NoProviderOutbox:
    def send_chatgpt_reply(self, **_kwargs):
        raise AssertionError("the provider-less outbox must not be selected")


def test_chatgpt_agent_fallback_targets_the_sending_user_queue(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    owner_name = store.owner().username
    alice, _ = store.create_user("alice", "alice-password")
    assert alice.username != owner_name

    # Both users registered the same legacy browser agent id: exactly the
    # shared baked identity that predates per-profile UUIDs.
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda _user: {"work": []})
    monkeypatch.setitem(adapters._chatgpt_records, (owner_name, "work"), {"agent_id": "vk-browser"})
    monkeypatch.setitem(adapters._chatgpt_records, (alice.username, "work"), {"agent_id": "vk-browser"})

    # Keep every queue/identity artifact inside this test's tmp directory.
    monkeypatch.setattr(agent_channel, "COMMANDS_FILE", tmp_path / "commands.jsonl")
    monkeypatch.setattr(agent_channel, "RESULTS_FILE", tmp_path / "results.jsonl")
    monkeypatch.setattr(agent_channel, "IDENTITY_DIR", tmp_path / "identity")

    conversation = store.create_conversation(
        "chatgpt:work", "chat-ref-alice", user_id=alice.user_id)
    store.bind_channel_route(
        user_id=alice.user_id, source="chatgpt:work", route_id="chatgpt:work")
    store.add_draft(ReplyDraft(
        id="d-fallback-1", conversation_id=conversation["id"],
        body="reply for alice", status="proposed"), user_id=alice.user_id)

    service = UserIOService(store, Generator(), NoProviderOutbox(),
                            chatgpt_outbox=ChatGPTWebOutbox())

    send_path_users: list[str] = []

    def blocked_web_send(self, *, chat_ref, draft_id, body, account_ref="", user):
        send_path_users.append(user)
        raise RuntimeError("POST /backend-api/conversation failed: 403")

    monkeypatch.setattr(ChatGPTWebOutbox, "_send_web", blocked_web_send)

    fallbacks: list[dict] = []
    real_enqueue = agent_channel.enqueue

    def recorded_call(agent_id, action, args=None, *, user, timeout_sec=60.0):
        queued = real_enqueue(
            {"agent_id": agent_id, "action": action, "args": args or {}}, user=user)
        # enqueue/poll key the queue by exactly (user, agent_id): prove which
        # user's browser would actually receive this command.
        to_sender = agent_channel.poll(agent_id, 0, user=alice.username)
        to_owner = agent_channel.poll(agent_id, 0, user=owner_name)
        fallbacks.append({
            "user": user, "agent_id": agent_id, "action": action,
            "args": args or {}, "to_sender": to_sender, "to_owner": to_owner,
            "queued_id": queued["id"],
        })
        return {"ok": True, "assistant_id": "asst_test"}

    monkeypatch.setattr(agent_channel, "call", recorded_call)

    draft = service.approve("d-fallback-1", user_id=alice.user_id)

    assert draft.status == "approved" and draft.receipt == "asst_test"
    # The server-side send path resolved the real sending principal.
    assert send_path_users == [alice.username]
    # The 403 browser fallback was enqueued once, into the sending user's queue.
    assert len(fallbacks) == 1
    call = fallbacks[0]
    assert call["user"] == alice.username and call["user"] != owner_name
    assert call["agent_id"] == "vk-browser"
    assert call["args"] == {"text": "reply for alice", "chat_ref": "chat-ref-alice"}
    # The owner's queue with the same agent id stayed untouched.
    assert call["to_owner"].get("command") is None
    command = call["to_sender"].get("command")
    assert command and command["action"] == "gpt_send" and command["agent_id"] == "vk-browser"
