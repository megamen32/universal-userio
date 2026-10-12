"""Fast unit: stale authenticated sockets recover reads, never mutations (5s/20s)."""
import json
import pytest
from universal_userio.channels.max_client import MaxClient, OP_GET_MESSAGE, OP_SEND


class Transport:
    def __init__(self, *, always_broken=False):
        self.connections = 0
        self.requests = []
        self.always_broken = always_broken

    def connect(self, timeout):
        self.connections += 1
        return type('Socket', (), {'connected': True, 'number': self.connections})()

    def send(self, ws, text):
        self.frame = json.loads(text)
        self.requests.append(self.frame['opcode'])
        if self.frame['opcode'] in (OP_GET_MESSAGE, OP_SEND) and (ws.number == 1 or self.always_broken):
            raise BrokenPipeError('remote idle close')

    def recv(self, ws, timeout):
        payload = {'profile': {'contact': {'id': 1}}} if self.frame['opcode'] == 19 else {'messages': [{'id': 17, 'text': 'source'}]}
        return dict(self.frame, cmd=1, payload=payload)

    def close(self, ws):
        ws.connected = False


def test_stale_socket_read_reconnects_same_session_once():
    transport = Transport()
    client = MaxClient('existing-token', 'existing-device', transport=transport)
    client.connect()
    assert client.get_message(chat_id=9, message_id=17)['id'] == 17
    assert transport.connections == 2
    assert transport.requests.count(OP_GET_MESSAGE) == 2
    assert OP_SEND not in transport.requests


def test_persistent_read_failure_is_bounded_and_clears_dead_socket():
    transport = Transport(always_broken=True)
    client = MaxClient('existing-token', 'existing-device', transport=transport)
    client.connect()
    with pytest.raises(BrokenPipeError):
        client.get_message(chat_id=9, message_id=17)
    assert transport.connections == 2
    assert not client.connected()


def test_mutation_roundtrip_is_never_replayed():
    transport = Transport()
    client = MaxClient('existing-token', 'existing-device', transport=transport)
    client.connect()
    with pytest.raises(BrokenPipeError):
        client._roundtrip(OP_SEND, {'chatId': 9, 'text': 'never replay'})
    assert transport.connections == 1
    assert transport.requests.count(OP_SEND) == 1
