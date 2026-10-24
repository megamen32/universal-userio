"""Host Asterisk numeric GID is explicit; no calls or providers."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]/'src/universal_userio/voice'
sys.path.insert(0,str(ROOT))
import agentcall_realtime_bridge as bridge


def test_incoming_socket_explicit_host_gid_preserves_private_mode(tmp_path,monkeypatch):
    path=tmp_path/'incoming.sock';path.touch();changes=[]
    monkeypatch.setenv('AGENTCALL_INCOMING_SOCKET_GID','147')
    config=bridge.BackendConfig.from_environment()
    monkeypatch.setattr(bridge.os,'chown',lambda path,uid,gid:changes.append((uid,gid)))
    bridge.set_incoming_socket_permissions(path,config.incoming_socket_gid)
    assert changes==[(-1,147)] and path.stat().st_mode&0o777==0o660


@pytest.mark.parametrize('value',['','0','-1','147.0',' 147','147 ','2147483648','true'])
def test_invalid_incoming_gid_rejected_before_listening(value,monkeypatch):
    monkeypatch.setenv('AGENTCALL_INCOMING_SOCKET_GID',value)
    with pytest.raises(ValueError):bridge.BackendConfig.from_environment()


def test_legacy_group_default_preserved(tmp_path,monkeypatch):
    monkeypatch.delenv('AGENTCALL_INCOMING_SOCKET_GID',raising=False)
    path=tmp_path/'incoming.sock';path.touch();changes=[]
    monkeypatch.setattr(bridge.grp,'getgrnam',lambda name:SimpleNamespace(gr_gid=163))
    monkeypatch.setattr(bridge.os,'chown',lambda path,uid,gid:changes.append((uid,gid)))
    config=bridge.BackendConfig.from_environment()
    bridge.set_incoming_socket_permissions(path,config.incoming_socket_gid)
    assert changes==[(-1,163)]
