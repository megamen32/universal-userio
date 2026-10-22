"""Read-only credential integration: expected 2 s, maximum 30 s, provider writes zero."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.mcp_catalog import TOOL_SPECS as CATALOG_TOOLS
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.mcp_surface import UserIOMcpSurface, TOOL_SPECS
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_readonly_named_token_http_and_mcp_deny_mutations(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'readonly.sqlite')
    alice, login = store.create_user('readonly-alice', 'correct test password')
    bob, _ = store.create_user('readonly-bob', 'another correct password')
    store.bind_channel_route(user_id=alice.user_id, source='max', route_id='readonly')
    service = UserIOService(store, object(), object())
    conversation, _ = service.receive(InboxMessage('max', 'm1', 'peer', 'current message', 1),
                                       route_id='readonly', user_id=alice.user_id)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service, token='service-token'))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, path, bearer, payload=None):
        req = Request(f'http://127.0.0.1:{server.server_port}'+path, method=method,
                      data=None if payload is None else json.dumps(payload).encode(),
                      headers={'Authorization':'Bearer '+bearer,'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        status, issued = call('POST', '/v1/personal-tokens', login,
                              {'name':'Secretary read only','scope':'read_only'})
        assert status == 201
        assert issued['scope'] == 'read_only'
        readonly = issued['token']
        principal = store.authenticate_token(readonly)
        assert principal.read_only
        assert not store.authenticate_token(login).read_only
        assert call('GET', '/v1/accounts', readonly)[0] == 200
        assert call('GET', '/v1/inbox', readonly)[0] == 200
        assert call('GET', '/v1/conversations/'+conversation, readonly)[1]['messages'][0]['body'] == 'current message'
        assert call('GET', '/v1/conversations/'+conversation, PersonalTokens(store).create(bob.user_id,'other')['token'])[0] == 404
        surface = UserIOMcpSurface(store, service)
        reads = {'userio.calls.telegram.get','userio.meetings.telemost.get','userio.calls.matrix.get','userio.messenger.events','userio.messenger.capabilities','userio.messenger.history','userio.messenger.get','userio.messenger.resolve','userio.messenger.media',
                 'userio.accounts.list','userio.channels.list','userio.channels.search',
                 'userio.channels.export','userio.channels.read',
                 'userio.channels.download','userio.inbox.list_new','userio.conversation.get',
                 'userio.workspace.poll','userio.workspace.claim_log','userio.workspace.exclusions.list',
                 'userio.workspace.policy.get','userio.workspace.policy.chats.list',
                 'userio.workspace.policy.evaluate','userio.workspace.triage.get'}
        names = {x['name'] for x in surface.tool_manifest(principal=principal)['tools']}
        assert names <= reads and 'userio.accounts.list' in names
        for spec in (*TOOL_SPECS, *CATALOG_TOOLS):
            if spec.name not in reads:
                assert surface.dispatch(spec.name, {}, principal=principal)['error'] == 'read_only_token'
                assert UserIOToolDispatcher(store,service).dispatch(spec.name,{},principal=principal)['error'] == 'read_only_token'
        for method, path in [('POST','/v1/drafts'),('POST','/v1/drafts/missing/approve'),
                             ('POST','/v1/messages/seen'),('POST','/v1/personal-tokens'),
                             ('POST','/v1/users'),('POST','/v1/workspace/claim'),
                             ('PATCH','/v1/drafts/missing'),('DELETE','/v1/accounts/missing'),
                             ('DELETE','/v1/personal-tokens/'+issued['id']),
                             ('GET','/v1/vault/sessions'),('GET','/v1/personal-tokens')]:
            assert call(method,path,readonly,{}) == (403,{'error':'read_only_token'})
        rpc = {'jsonrpc':'2.0','id':1,'method':'tools/call',
               'params':{'name':'userio.accounts.list','arguments':{}}}
        assert call('POST','/mcp',readonly,rpc)[1]['result']['structuredContent']['ok']
        rpc['params']['name'] = 'userio.message.mark_seen'
        assert call('POST','/mcp',readonly,rpc)[1]['result']['isError']
        assert call('POST','/v1/personal-tokens',login,{'name':'Full access'})[1]['scope'] == 'full_access'
        assert call('GET','/v1/personal-tokens',login)[1]['tokens'][0]['scope'] in {'read_only','full_access'}
        assert call('DELETE','/v1/personal-tokens/'+issued['id'],login) == (200,{'revoked':True})
        assert call('GET','/v1/accounts',readonly)[0] == 401
        assert store.pending_drafts(user_id=alice.user_id) == []
        assert store.conversation(conversation,user_id=alice.user_id)['messages'][0]['seen_at'] is None
    finally:
        server.shutdown(); server.server_close()


def test_scope_validation_and_persistence(tmp_path):
    path = tmp_path/'tokens.sqlite'
    store = SQLiteUserIOStore(path)
    tokens = PersonalTokens(store)
    for scope in ('unsupported',None,[],True):
        with pytest.raises(ValueError):
            tokens.create(store.owner().user_id,'bad',scope=scope)
    issued = tokens.create(store.owner().user_id,'reader',scope='read_only')
    assert SQLiteUserIOStore(path).authenticate_token(issued['token']).read_only
