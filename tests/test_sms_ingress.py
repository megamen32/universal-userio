from universal_userio.contracts import InboxMessage
from universal_userio.sms_ingress import poll_once


class Gateway:
    def inbound(self):
        return [InboxMessage("sms", "sms-1", "+15550001111", "hello", 1.0)]


class Sink:
    def __init__(self): self.calls=[]
    def send_message(self, **kwargs): self.calls.append(kwargs)


def test_sms_ingress_posts_gateway_messages_to_userio():
    sink=Sink()
    assert poll_once(sink, Gateway()) == 1
    assert sink.calls == [{"source":"sms","account_id":"sms","route_id":"sms","message_id":"sms-1","sender":"+15550001111","body":"hello"}]
