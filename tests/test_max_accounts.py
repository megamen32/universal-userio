"""Personal MAX enrollment: focused integration, expected 2s, maximum 10s.

Detect cross-user login/session reuse, expired socket leaks, ambiguous login
replay, and messages being stored under the operator's account.
"""
from types import SimpleNamespace
import pytest
from universal_userio.max_accounts import MaxAccounts, owned_client, poll_personal_max
from universal_userio.account_connections import ConnectorSecrets
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Flow:
    instances = []
    def __init__(self, expected, **kwargs):
        assert expected == ''
        self.closed = False
        self.fail = False
        self.finish_calls = 0
        self.instances.append(self)
    def begin(self):
        return dict(qrLink='https://max.ru/:auth/11111111-1111-1111-1111-111111111111', ttl=120000, pollingInterval=5000)
    def poll(self): return True
    def finish(self):
        self.finish_calls += 1
        if self.fail: raise TimeoutError('fixture unknown login')
        return dict(user_id='77', token='fixture-personal-token', device_id='fixture-device')
    def close(self): self.closed = True


class Client:
    def __init__(self, *args): self.closed = False
    def connect(self): return dict(profile=dict(contact=dict(id=77, firstName='Test')))
    def close(self): self.closed = True


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('USERIO_CONNECTION_SECRETS_DIR', str(tmp_path/'secrets'))
    store = SQLiteUserIOStore(tmp_path/'store.db')
    user, _ = store.create_user('max-personal-fixture', 'fixture-only-password')
    service = UserIOService(store, object(), object())
    manager = MaxAccounts(service, flow_factory=Flow, client_factory=Client, clock=lambda: 1000)
    return store, service, user.user_id, manager


def test_login_is_owned_and_persists_exact_identity(tmp_path, monkeypatch):
    store, service, uid, manager = setup(tmp_path, monkeypatch)
    pending = manager.begin(uid)
    with pytest.raises(ValueError): manager.poll(store.default_user_id, pending['login_id'])
    assert manager.poll(uid, pending['login_id'])['id'] == 'max:77'
    account, = store.accounts(user_id=uid)
    secret = ConnectorSecrets().load(account['credential_ref'], user_id=uid, provider='max')
    assert secret['account_id'] == 'max:77' and secret['max_user_id'] == '77'
    assert secret['user_id'] == uid and account['capabilities'] == ['read', 'reply']
    assert store.accounts() == []
    with pytest.raises(ValueError): manager.poll(uid, pending['login_id'])
    assert Flow.instances[-1].finish_calls == 1 and Flow.instances[-1].closed


def test_unknown_login_is_consumed_and_old_account_retained(tmp_path, monkeypatch):
    store, service, uid, manager = setup(tmp_path, monkeypatch)
    store.register_account(account_id='max:77', provider='max', display_name='old',
        can_read=True, can_reply=True, credential_ref='env:old', user_id=uid)
    pending = manager.begin(uid); flow = Flow.instances[-1]; flow.fail = True
    with pytest.raises(TimeoutError): manager.poll(uid, pending['login_id'])
    with pytest.raises(ValueError): manager.poll(uid, pending['login_id'])
    assert flow.finish_calls == 1 and flow.closed
    assert store.accounts(user_id=uid)[0]['credential_ref'] == 'env:old'


def test_expiry_closes_pending_without_another_browser_request(tmp_path, monkeypatch):
    _, _, uid, manager = setup(tmp_path, monkeypatch)
    manager.begin(uid); flow = Flow.instances[-1]
    manager.clock = lambda: 1121
    manager.expire()
    assert flow.closed and not manager.pending


def test_owned_client_never_reuses_foreign_or_replaced_credential(tmp_path, monkeypatch):
    store, service, uid, manager = setup(tmp_path, monkeypatch)
    monkeypatch.setattr('universal_userio.max_accounts.native_client', Client)
    manager.poll(uid, manager.begin(uid)['login_id'])
    first = owned_client(service, uid, 'max:77')
    assert owned_client(service, uid, 'max:77') is first
    with pytest.raises(ValueError): owned_client(service, store.default_user_id, 'max:77')
    manager.poll(uid, manager.begin(uid)['login_id'])
    assert owned_client(service, uid, 'max:77') is not first and first.closed


def test_personal_sync_scopes_ids_and_imports_edits_without_duplicate_live_event(tmp_path, monkeypatch):
    store, service, uid, manager = setup(tmp_path, monkeypatch)
    manager.poll(uid, manager.begin(uid)['login_id'])
    account = store.accounts(user_id=uid)[0]
    record = ConnectorSecrets().load(account['credential_ref'], user_id=uid, provider='max')
    class Reader(Client):
        rows = [{'id': '123', 'sender': '88', 'text': 'before', 'time':1700000000000}]
        def chats(self, **kw): return [{'id':9, 'type':'DIALOG'}]
        def history(self, peer, **kw): return [dict(r) for r in self.rows]
    monkeypatch.setattr('universal_userio.max_accounts.native_client', Reader)
    poll_personal_max(service, record)
    message = store.message('max:77|9:123', source='max', user_id=uid)
    assert message['body'] == 'before' and message['direction'] == 'incoming'
    Reader.rows[0].update(text='after', updateTime=1700000001000)
    Reader.rows.append(dict(id='124',sender='77',text='phone outgoing',time=1700000000000))
    poll_personal_max(service, record)
    assert store.message('max:77|9:123', source='max', user_id=uid)['body'] == 'after'
    assert store.message('max:77|9:124', source='max', user_id=uid)['direction'] == 'outgoing'
    assert store.message('max:77|9:123', source='max', user_id=store.default_user_id) is None


def test_registration_failure_drops_only_new_staged_secret(tmp_path, monkeypatch):
    store, service, uid, manager = setup(tmp_path, monkeypatch)
    def fail(**kw): raise RuntimeError('fixture database failure')
    monkeypatch.setattr(store, 'register_account', fail)
    pending = manager.begin(uid)
    with pytest.raises(RuntimeError): manager.poll(uid, pending['login_id'])
    assert list((tmp_path/'secrets').glob('*.json')) == []
    assert store.accounts(user_id=uid) == [] and not manager.pending


def test_http_begin_cancel_is_personal_and_foreign_cancel_is_denied(tmp_path, monkeypatch):
    import json, threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from universal_userio.http_api import handler
    from universal_userio.personal_tokens import PersonalTokens
    store, service, uid, manager = setup(tmp_path, monkeypatch)
    _, token = store.login('max-personal-fixture', 'fixture-only-password')
    _, foreign = store.create_user('max-foreign-fixture', 'fixture-only-password')
    readonly = PersonalTokens(store).create(uid, 'fixture readonly', scope='read_only')['token']
    monkeypatch.setattr('universal_userio.max_accounts.MaxAccounts', lambda _: manager)
    server = ThreadingHTTPServer(('127.0.0.1',0), handler(service,token='fixture-owner'))
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(action, bearer, payload={}):
        req=Request(f'http://127.0.0.1:{server.server_port}/v1/max/connections/'+action,
            data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+bearer,'Content-Type':'application/json'})
        try:
            with urlopen(req) as r:return r.status,json.load(r)
        except HTTPError as r:return r.code,json.load(r)
    try:
        assert request('begin','')[0] == 401
        assert request('begin',readonly)[0] == 403
        status,pending=request('begin',token);assert status==200
        assert request('cancel','fixture-owner',{'login_id':pending['login_id']})[0]==403
        assert request('cancel',foreign,{'login_id':pending['login_id']})[0]==400
        assert request('cancel',token,{'login_id':pending['login_id']})==(200,{'cancelled':True})
        assert Flow.instances[-1].closed and not manager.pending
    finally:manager.close();server.shutdown();server.server_close()
