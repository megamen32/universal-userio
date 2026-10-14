"""Focused integration, expected 2s/max20s: recorder may never send/read inbox."""
import pytest

from universal_userio.personal_tokens import PersonalTokens
from universal_userio.store import SQLiteUserIOStore
from universal_userio.service import UserIOService
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.token_permissions import MEETING_RECORDING_TOOLS, meeting_recorder_http_allowed


def test_recorder_scope_manifest_and_default_deny(tmp_path):
    store = SQLiteUserIOStore(tmp_path/'scope.sqlite')
    owner, login = store.create_user('recorder-owner', 'a correct test password')
    issued = PersonalTokens(store).create(owner.user_id, 'Recorder', scope='meeting_recording')
    principal = store.authenticate_token(issued['token'])
    assert principal.meeting_recorder and not principal.read_only
    assert not store.authenticate_token(login).meeting_recorder
    service = UserIOService(store, object(), object())
    surface = UserIOMcpSurface(store, service)
    assert {x['name'] for x in surface.tool_manifest(principal=principal)['tools']} == MEETING_RECORDING_TOOLS
    assert surface.resource_manifest(principal=principal) == {'resources': []}
    with pytest.raises(PermissionError):
        surface.read_resource('userio://accounts', principal=principal)
    for name in ('userio.channels.read','userio.channels.send','userio.messenger.approve','userio.calls.telegram.start','userio.workspace.poll'):
        assert surface.dispatch(name, {}, principal=principal)['error'] == 'meeting_recording_scope_only'
        assert UserIOToolDispatcher(store,service).dispatch(name, {}, principal=principal)['error'] == 'meeting_recording_scope_only'
    assert PersonalTokens(store).revoke(owner.user_id, issued['id'])
    assert store.authenticate_token(issued['token']) is None


def test_recorder_http_is_limited_to_meetings():
    for method, path in [('POST','/mcp'),('GET','/v1/meetings/telemost'),('POST','/v1/meetings/telemost'),('GET','/v1/meetings/telemost/id'),('POST','/v1/meetings/telemost/id/stop'),('GET','/v1/meetings/telemost/id/audio/track')]:
        assert meeting_recorder_http_allowed(method,path)
    for method, path in [('GET','/v1/accounts'),('POST','/v1/personal-tokens'),('GET','/v1/personal-tokens'),('POST','/v1/conversations/id/send'),('DELETE','/v1/meetings/telemost/id'),('POST','/v1/meetings/telemost-other'),('PATCH','/v1/meetings/telemost/id'),('GET','/v1/meetings/telemost/id/audio/track/extra')]:
        assert not meeting_recorder_http_allowed(method,path)
