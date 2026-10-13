"""Fast unit expected1/max5s: normal QR source/identity/no-replay contracts."""
import pytest
from universal_userio.max_qr_login import MaxQrLogin


class Client:
    def __init__(self, *, user='900000102', available=False):
        self.calls=[];self.user=user;self.available=available;self.closed=False
    def initialize_anonymous(self):self.calls.append(6)
    def _roundtrip(self, opcode, payload):
        self.calls.append(opcode)
        if opcode==288:return {'trackId':'private-track','qrLink':'https://max.ru/test-qr','ttl':60000,'pollingInterval':1000}
        if opcode==289:return {'status':{'loginAvailable':self.available,'ttl':55000}}
        if opcode==291:return {'profile':{'contact':{'id':int(self.user)}},'tokenAttrs':{'LOGIN':{'token':'private-test-token-never-print'}}}
        raise AssertionError('unapproved opcode')
    def close(self):self.closed=True


def test_qr_requires_real_phone_confirmation_before_token_exchange():
    client=Client();flow=MaxQrLogin('900000102',client=client)
    pending=flow.begin();assert pending['ttl']==60000
    assert flow.poll() is False
    with pytest.raises(ValueError,match='human MAX confirmation'):flow.finish()
    assert client.calls==[6,288,289]
    flow.close();assert client.closed


def test_qr_auth_binds_expected_account_and_finishes_once():
    client=Client(available=True);flow=MaxQrLogin('900000102',client=client)
    flow.begin();assert flow.poll() is True
    credential=flow.finish();assert credential['user_id']=='900000102' and credential['device_id']
    assert credential['token']=='private-test-token-never-print'
    with pytest.raises(ValueError,match='must not replay'):flow.finish()
    assert client.calls==[6,288,289,291]


def test_qr_rejects_different_authenticated_account_without_retry():
    client=Client(user='12345',available=True);flow=MaxQrLogin('900000102',client=client)
    flow.begin();flow.poll()
    with pytest.raises(ValueError,match='differs from expected'):flow.finish()
    with pytest.raises(ValueError,match='must not replay'):flow.finish()
    assert client.calls==[6,288,289,291]


def test_qr_cannot_restart_an_existing_challenge():
    client=Client();flow=MaxQrLogin('900000102',client=client)
    flow.begin()
    with pytest.raises(ValueError,match='already started'):flow.begin()
    assert client.calls==[6,288]


def test_real_anonymous_transport_never_sends_existing_account_login():
    from universal_userio.channels.max_client import MaxClient
    from test_channels_max import ScriptedTransport, _frame

    transport=ScriptedTransport({
        6:_frame(6,payload={'lang':'ru'}),
        288:_frame(288,payload={'trackId':'track','qrLink':'https://max.ru/qr',
                              'ttl':60000,'pollingInterval':1000}),
        289:_frame(289,payload={'status':{'loginAvailable':False}}),
    })
    client=MaxClient('', 'isolated-device',transport=transport)
    flow=MaxQrLogin('900000102',client=client)
    try:
        flow.begin()
        assert flow.poll() is False
        assert [frame['opcode'] for frame in transport.sent]==[6,288,289]
    finally:
        flow.close()


def test_anonymous_initialization_rejects_existing_sender():
    from universal_userio.channels.max_client import MaxClient
    from test_channels_max import ScriptedTransport

    transport=ScriptedTransport({})
    client=MaxClient('existing-token','existing-device',transport=transport)
    with pytest.raises(ValueError,match='isolated anonymous'):
        client.initialize_anonymous()
    assert transport.sent==[]


def test_disconnected_qr_session_cannot_fall_back_to_sender_login():
    from universal_userio.channels.max_client import MaxClient
    from test_channels_max import ScriptedTransport, _frame

    transport=ScriptedTransport({6:_frame(6,payload={'lang':'ru'}),
        288:_frame(288,payload={'trackId':'track','qrLink':'https://max.ru/qr',
                             'ttl':60000,'pollingInterval':1000})})
    client=MaxClient('','isolated-device',transport=transport)
    flow=MaxQrLogin('900000102',client=client)
    try:
        flow.begin()
        client._ws.connected=False
        with pytest.raises(RuntimeError,match='QR session disconnected'):
            flow.poll()
        assert [frame['opcode'] for frame in transport.sent]==[6,288]
    finally:
        flow.close()
