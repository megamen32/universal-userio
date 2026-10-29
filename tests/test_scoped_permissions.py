"""Fast unit: token boundaries, per-chat writes and live permissions updates (20s/max200s)."""
import pytest
from universal_userio.contracts import InboxMessage
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.scoped_permissions import enforce, grant_for
from universal_userio.store import SQLiteUserIOStore
from universal_userio.contracts import ConversationPolicy


def fixture(tmp_path):
    s = SQLiteUserIOStore(tmp_path / 'state.sqlite3')
    user = s.default_user_id
    # Register finite fixture accounts through the same store boundary.
    s.register_account(account_id='telegram:1',provider='telegram',display_name='Telegram',enabled=True,
        can_read=True,can_reply=True,credential_ref='fixture',user_id=user)
    for cid, peer in [('chat1','101'),('chat2','102')]:
        s.ingest(InboxMessage('telegram','telegram:1|'+peer+':1','Катя-'+peer,'Привет',100,
            peer_id=peer,conversation_kind='direct'),conversation_id=cid,
            policy=ConversationPolicy('fixture'),account_ref='telegram:1',user_id=user)
    t = PersonalTokens(s)
    grants=[{'account_ref':'telegram:1','access':'read_write','chats':[
        {'conversation_id':'chat1','access':'read_only'}, {'conversation_id':'chat2','access':'read_write'}]}]
    created=t.create(user,'Fixture',scope='scoped_messenger',grants=grants)
    return s,t,created,s.authenticate_token(created['token'])


def test_chat_read_write_and_account_denial(tmp_path):
    s,t,created,p = fixture(tmp_path)
    enforce(s,p,'userio.messenger.history',{'account_ref':'telegram:1','conversation_id':'chat1'})
    with pytest.raises(PermissionError,match='read_only'):
        enforce(s,p,'userio.messenger.propose',{'account_ref':'telegram:1','conversation_id':'chat1'})
    enforce(s,p,'userio.messenger.propose',{'account_ref':'telegram:1','conversation_id':'chat2'})
    with pytest.raises(PermissionError):
        enforce(s,p,'userio.messenger.history',{'account_ref':'telegram:1','conversation_id':'chat3'})
    with pytest.raises(PermissionError):
        enforce(s,p,'userio.messenger.history',{'account_ref':'telegram:other','conversation_id':'chat2'})
    with pytest.raises(PermissionError):
        enforce(s,p,'userio.calls.telegram.start',{'account_ref':'telegram:1'})
    with pytest.raises(PermissionError):
        enforce(s,p,'userio.workspace.poll',{})


def test_contact_resolution_requires_account_wide_grant(tmp_path):
    from universal_userio.service import UserIOService
    from universal_userio.mcp_surface import UserIOMcpSurface
    s, t, created, p = fixture(tmp_path)
    surface = UserIOMcpSurface(s, UserIOService(s, object(), object()))
    result = surface.dispatch('userio.contacts.resolve', {
        'account_ref': 'telegram:1', 'identifier': 'unlisted-contact',
    }, principal=p)
    assert result == {'ok': False, 'error': 'account_scope_required_for_contact_resolution'}
    enforce(s, p, 'userio.messenger.resolve', {'account_ref': 'telegram:1', 'peer_id': '101'})
    with pytest.raises(PermissionError):
        enforce(s, p, 'userio.messenger.resolve', {'account_ref': 'telegram:1', 'peer_id': '999'})
    t.update_permissions(p.user_id, created['id'], [
        {'account_ref': 'telegram:1', 'access': 'read_only', 'chats': None},
    ])
    enforce(s, s.authenticate_token(created['token']), 'userio.contacts.resolve',
            {'account_ref': 'telegram:1', 'identifier': 'unlisted-contact'})


def test_existing_token_changes_without_rotation(tmp_path):
    s,t,created,p = fixture(tmp_path)
    t.update_permissions(p.user_id,created['id'],[{'account_ref':'telegram:1','access':'read_only','chats':[
        {'conversation_id':'chat1','access':'read_only'}]}])
    current=s.authenticate_token(created['token'])
    assert grant_for(current,'telegram:1','chat1')=='read_only'
    assert grant_for(current,'telegram:1','chat2') is None
    assert t.list(p.user_id)[0]['grants'][0]['chats'][0]['conversation_id']=='chat1'


@pytest.mark.parametrize('grants',[
    [{'account_ref':'telegram:other','access':'read_only','chats':None}],
    [{'account_ref':'telegram:1','access':'read_only','chats':[{'conversation_id':'chat1','access':'read_write'}]}],
    [{'account_ref':'telegram:1','access':'read_only','chats':[]}],
    [{'account_ref':'telegram:1','access':'read_only','chats':[{'conversation_id':'foreign','access':'read_only'}]}],
])
def test_invalid_grants_are_rejected(tmp_path,grants):
    s,t,created,p = fixture(tmp_path)
    with pytest.raises(ValueError):
        t.create(p.user_id,'invalid',scope='scoped_messenger',grants=grants)


def test_actual_mcp_filters_events_and_blocks_write_before_provider(tmp_path):
    from universal_userio.service import UserIOService
    from universal_userio.mcp_surface import UserIOMcpSurface
    s,t,created,p = fixture(tmp_path)
    t.update_permissions(p.user_id,created['id'],[{'account_ref':'telegram:1','access':'read_only','chats':[
        {'conversation_id':'chat1','access':'read_only'}]}])
    p=s.authenticate_token(created['token'])
    service=UserIOService(s,object(),object())
    surface=UserIOMcpSurface(s,service)
    result=surface.dispatch('userio.messenger.events',{'account_ref':'telegram:1','after':0},principal=p)
    assert result['ok']
    assert len(result['events'])==1 and result['events'][0]['message']['source']['conversation_id']=='chat1'
    denied=surface.dispatch('userio.messenger.propose',{'account_ref':'telegram:1','conversation_id':'chat1'},principal=p)
    assert denied=={'ok':False,'error':'chat_read_only_token'}
    hidden=surface.dispatch('userio.messenger.history',{'account_ref':'telegram:1','conversation_id':'chat2'},principal=p)
    assert hidden=={'ok':False,'error':'chat_or_account_outside_token_scope'}
    tools={x['name'] for x in surface.tool_manifest(principal=p)['tools']}
    assert 'userio.accounts.list' in tools and 'userio.messenger.history' in tools
    assert 'userio.messenger.propose' not in tools and 'userio.calls.telegram.start' not in tools
