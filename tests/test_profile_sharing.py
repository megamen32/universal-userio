import pytest
from universal_userio.contracts import ConversationPolicy, InboxMessage, UserPrincipal
from universal_userio.store import SQLiteUserIOStore
from universal_userio.service import UserIOService
from universal_userio.mcp_surface import UserIOMcpSurface


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / 'db.sqlite')
    service_owner = store.owner()
    owner = UserPrincipal(service_owner.user_id, service_owner.username, service_owner.role)
    recipient, _ = store.create_user('recipient', 'a sufficiently long password')
    outsider, _ = store.create_user('outsider', 'a sufficiently long password')
    store.register_account(account_id='telegram:1', provider='telegram', display_name='test', enabled=True,
        can_read=True, can_reply=True, credential_ref='telegram-qr:fixture', user_id=owner.user_id)
    setup_service = UserIOService(store, object(), object())
    for peer in ('101', '102'):
        setup_service.receive(InboxMessage('telegram', f'telegram:1|{peer}:1', peer, f'text {peer}', 100,
                     peer_id=peer, conversation_kind='direct'), route_id='telegram', account_ref='telegram:1')
    active = {'101'}
    monkeypatch.setattr('universal_userio.profile_sharing._folder', lambda account, folder: set(active))
    surface = UserIOMcpSurface(store, UserIOService(store, object(), object()))
    return store, owner, recipient, outsider, surface, active


def run(surface, actor, name, **args):
    return surface.dispatch('userio.profile_share.' + name, args, principal=actor)


def create(env):
    store, owner, recipient, _, surface, _ = env
    return run(surface, owner, 'create', grantee_id=recipient.user_id, account_ref='telegram:1', folder_id=16, peers=['101'])['share_id']


def test_identity_exact_peers_live_folder_and_revoke(env):
    store, owner, recipient, outsider, surface, active = env
    share = create(env)
    allowed = store._connection.execute("SELECT id FROM conversations WHERE peer_id='101'").fetchone()['id']
    private = store._connection.execute("SELECT id FROM conversations WHERE peer_id='102'").fetchone()['id']
    def call(actor, chat):
        return run(surface, actor, 'call', share_id=share, operation='userio.messenger.history',
                   arguments={'account_ref':'telegram:1','conversation_id':chat})
    assert call(recipient, allowed)['ok'] is True
    assert call(recipient, private)['ok'] is False
    assert call(outsider, allowed)['error'] == 'share_not_found'
    active.clear()
    assert call(recipient, allowed)['ok'] is False
    active.add('101')
    assert run(surface, outsider, 'revoke', share_id=share)['ok'] is False
    assert run(surface, owner, 'revoke', share_id=share)['revoked'] is True
    assert call(recipient, allowed)['error'] == 'share_not_found'
    rows = store._connection.execute('SELECT actor_id,operation,outcome FROM profile_share_audit WHERE share_id=?', (share,)).fetchall()
    assert any(r['actor_id'] == recipient.user_id and r['operation'] == 'userio.messenger.history' and r['outcome'] == 'ok' for r in rows)
    assert any(r['operation'] == 'revoke' for r in rows)


def test_grant_rejects_foreign_peers_and_unbound_actions(env):
    store, owner, recipient, outsider, surface, _ = env
    assert run(surface, owner, 'create', grantee_id=recipient.user_id, account_ref='telegram:1', folder_id=16, peers=['102'])['error'] == 'peer_outside_folder'
    share = create(env)
    for operation, arguments in [
        ('userio.messenger.approve', {'action_id':'action_foreign','expected_hash':'x','confirm':True}),
        ('userio.messenger.propose', {'account_ref':'telegram:1','conversation_id':'allowed','action':'react'}),
        ('userio.workspace.poll', {}),
    ]:
        assert run(surface, recipient, 'call', share_id=share, operation=operation, arguments=arguments)['ok'] is False


def test_folder_authority_errors_are_fail_closed(env, monkeypatch):
    _, _, recipient, _, surface, _ = env
    share = create(env)
    def unavailable(*args):
        raise ValueError('folder_authority_unavailable')
    monkeypatch.setattr('universal_userio.profile_sharing._folder', unavailable)
    assert run(surface, recipient, 'call', share_id=share, operation='userio.channels.list', arguments={})['error'] == 'folder_authority_unavailable'


def test_reply_proposal_approval_and_receipt_use_one_fake_provider(env):
    store, owner, recipient, _, surface, _ = env
    class FakeOutbox:
        def __init__(self): self.calls = []
        def reply_message(self, **kwargs):
            self.calls.append(kwargs)
            return {'ok': True, 'account_id': kwargs['account_ref'], 'peer_id': kwargs['chat_id'],
                    'message_id': '78', 'reply_to_id': kwargs['message_id'],
                    'thread_id': kwargs['thread_id'], 'text': kwargs['text'], 'date': 123, 'out': True}
    outbox = FakeOutbox()
    surface._service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    share = create(env)
    allowed = store._connection.execute("SELECT id FROM conversations WHERE peer_id='101'").fetchone()['id']
    args = {'account_ref': 'telegram:1', 'conversation_id': allowed,
            'source_message_id': 'telegram:1|101:1', 'action': 'reply',
            'payload': {'text':'test response'}, 'request_id':'fixture-reply-0000001'}
    proposal = run(surface, recipient, 'call', share_id=share, operation='userio.messenger.propose', arguments=args)
    assert proposal['ok'], proposal
    action = proposal['action']
    assert not outbox.calls
    approval = {'action_id':action['id'], 'expected_hash':action['hash'], 'confirm':True}
    result = run(surface, recipient, 'call', share_id=share, operation='userio.messenger.approve', arguments=approval)
    assert result['ok'], result
    assert result['action']['status'] == 'accepted'
    assert len(outbox.calls) == 1 and outbox.calls[0]['chat_id'] == '101'
    repeated = run(surface, recipient, 'call', share_id=share, operation='userio.messenger.approve', arguments=approval)
    assert repeated['ok'] and len(outbox.calls) == 1, repeated


def test_restrictions_and_audit_are_bound_to_authenticated_user(env):
    store, owner, recipient, outsider, surface, active = env
    share = create(env)
    from universal_userio.contracts import UserPrincipal
    restricted = UserPrincipal(recipient.user_id, recipient.username, recipient.role, read_only=True)
    assert run(surface, restricted, 'list')['error'] == 'full_profile_required'
    assert run(surface, outsider, 'audit', share_id=share)['error'] == 'share_not_found'
    assert run(surface, owner, 'audit', share_id=share)['events'][0]['operation'] == 'create'
    assert run(surface, recipient, 'call', share_id=share, operation='userio.messenger.get',
               arguments={'action_id':'action_owned_by_someone_else'})['error'] == 'share_action_not_found'


def test_live_folder_filter_fails_closed_on_dynamic_categories(monkeypatch):
    import io, json
    from universal_userio.profile_sharing import _folder
    monkeypatch.setenv('USERIO_TELEGRAM_QR_URL', 'http://fixture')
    monkeypatch.setenv('USERIO_TELEGRAM_QR_TOKEN', 'fixture')
    folder = {'id':16, 'include_peer_ids':['101','102'], 'pinned_peer_ids':[], 'exclude_peer_ids':['102'],
              'categories':{'groups':False}, 'exclude_muted':False, 'exclude_read':False, 'exclude_archived':False}
    def response(*args, **kwargs):
        return io.BytesIO(json.dumps({'account_id':'telegram:1','folders':[folder]}).encode())
    monkeypatch.setattr('urllib.request.urlopen', response)
    assert _folder('telegram:1',16) == {'101'}
    folder['categories']['groups'] = True
    with pytest.raises(ValueError, match='folder_authority_unavailable'):
        _folder('telegram:1',16)


def test_multiple_local_conversations_for_same_peer_are_granted(env):
    store, owner, recipient, _, surface, _ = env
    store.ingest(InboxMessage('telegram', 'telegram:1|101:42', 'another local sender', 'more history', 101,
                 peer_id='101', conversation_kind='direct'), conversation_id='second-local-record',
                 policy=ConversationPolicy('fixture'), account_ref='telegram:1', user_id=owner.user_id)
    share = create(env)
    result = run(surface, recipient, 'call', share_id=share, operation='userio.messenger.history',
                 arguments={'account_ref':'telegram:1', 'conversation_id':'second-local-record'})
    assert result['ok'], result
