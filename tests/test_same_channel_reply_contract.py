from dataclasses import replace
import unittest

from universal_userio.same_channel_reply import EventReplyConsent, ReplyOrigin, SameChannelReplyRequest, authorize_reply


class SameChannelReplyContractTest(unittest.TestCase):
    def event(self, source="matrix", peer="!one:example.org"):
        return {"seq": 7, "source": source, "account_ref": "matrix",
                "conversation_id": "conv_current", "peer_id": peer,
                "sender": "@person:example.org", "message_id": "!one:example.org:$current",
                "provider_message_id": "$current", "body": "Hello", "edited_at": 0,
                "direction": "incoming", "conversation_kind": "group",
                "body_truncated": False, "sender_is_bot": False}

    def test_room_account_and_current_event_are_immutable(self):
        first = ReplyOrigin.from_event(self.event(), user_id="owner")
        other = ReplyOrigin.from_event(self.event(peer="!two:example.org"), user_id="owner")
        self.assertNotEqual(first.conversation_key(), other.conversation_key())
        event = self.event()
        event["reply_to_message_id"] = "$quoted_old_event"
        self.assertEqual(ReplyOrigin.from_event(event, user_id="owner").reply_target, "$current")

    def test_legacy_or_truncated_events_fail_closed(self):
        for change in ({"peer_id": ""}, {"account_ref": ""}, {"provider_message_id": ""},
                       {"body_truncated": True}, {"direction": "outgoing"}, {"sender_is_bot": True}, {"user_id": "foreign"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ReplyOrigin.from_event({**self.event(), **change}, user_id="owner")

    def test_same_channel_consent_is_not_general_outbound_authority(self):
        origin = ReplyOrigin.from_event(self.event(), user_id="owner")
        request = SameChannelReplyRequest(origin, "reply-request-1", "Спасибо, проверю.")
        consent = EventReplyConsent(origin, "secretary", 200, True)
        account = {"id": "matrix", "provider": "matrix", "enabled": True, "capabilities": ["read", "reply"]}
        args = {"current_origin": origin, "consent": consent, "worker_id": "secretary", "now": 100,
                "account": account, "send_enabled": True}
        authorize_reply(request, **args)
        for change in ({"current_origin": replace(origin, peer_id="!foreign:example.org")},
                       {"current_origin": replace(origin, body_sha256="0" * 64)},
                       {"current_origin": replace(origin, edited_at=1)},
                       {"consent": replace(consent, allow_ordinary_reply=False)}, {"now": 200},
                       {"worker_id": "other-worker"}, {"send_enabled": False},
                       {"account": {**account, "capabilities": ["read"]}},
                       {"account": {**account, "provider": "telegram"}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                authorize_reply(request, **{**args, **change})

    def test_sms_requires_exact_account_and_originating_number(self):
        event = {**self.event(), "source": "sms", "account_ref": "sms:device-a",
                 "peer_id": "+70000000001", "sender": "+70000000001", "message_id": "sms-81",
                 "provider_message_id": "81",
                 "conversation_kind": "direct"}
        origin = ReplyOrigin.from_event(event, user_id="owner")
        self.assertEqual(origin.peer_id, origin.sender_id)
        with self.assertRaises(ValueError):
            ReplyOrigin.from_event({**event, "peer_id": "+70000000002"}, user_id="owner")


if __name__ == "__main__":
    unittest.main()
