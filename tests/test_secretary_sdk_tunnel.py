"""The private transport never adopts another app or a stale credential."""
import base64
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('sdk_tunnel', Path(__file__).resolve().parents[1] / 'scripts/secretary_sdk_tunnel.py')
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


def snapshot(**claims):
    body = {'agent_id': tunnel.APP_ID, 'sub': tunnel.APP_ID, 'exp': 2000, 'token_version': 7, **claims}
    encoded = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip('=')
    return {'address': '192.168.128.7', 'token': 'fixture.' + encoded + '.fixture',
            'containerId': 'fixed-container', 'imageId': 'fixed-image'}


def test_exact_app_upstream_and_fresh_token_are_accepted():
    value = snapshot()
    assert tunnel.validate(value) == ('192.168.128.7', value['token'], 7)


@pytest.mark.parametrize('claims', [{'agent_id': 'another-app'}, {'sub': 'another-app'}])
def test_other_app_token_is_denied_before_forwarding(claims):
    with pytest.raises(ValueError):
        tunnel.validate(snapshot(**claims))


def test_public_or_loopback_upstream_is_denied():
    for address in ('8.8.8.8', '127.0.0.1'):
        value = dict(snapshot(), address=address)
        with pytest.raises(ValueError):
            tunnel.validate(value)


def test_native_opaque_bearer_does_not_invent_a_jwt_expiry_gate():
    value = snapshot(exp=1)
    assert tunnel.validate(value)[1] == value["token"]


def test_live_app_token_rotation_refreshes_protected_reference_without_replay(tmp_path, monkeypatch):
    import subprocess
    old = snapshot()
    new = snapshot(token_version=8)
    new['containerId'] = 'replacement-fixed-app'
    values = iter([old, new])
    monkeypatch.setattr(tunnel, 'STATE', tmp_path)
    tmp_path.chmod(0o700)
    monkeypatch.setattr(tunnel, 'resolve_snapshot', lambda: next(values))
    class Child:
        count = 0
        def wait(self, timeout):
            self.count += 1
            if self.count == 1:
                raise subprocess.TimeoutExpired('fixture SSH', timeout)
            return 0
        def poll(self): return 0
    starts = []
    monkeypatch.setattr(tunnel, 'start_forward', lambda address: starts.append(address) or Child())
    assert tunnel.supervise() == 0
    assert (tmp_path / 'voice.token').read_text().strip() == new['token']
    assert starts == [old['address']]
    assert json.loads((tmp_path / 'upstream.json').read_text())['tokenVersion'] == 8


def test_ip_change_waits_for_idle_before_reconnecting(tmp_path, monkeypatch):
    import subprocess
    old, new = snapshot(), dict(snapshot(token_version=8), address='192.168.128.8')
    values, idles = iter([old, new, new]), iter([False, True])
    monkeypatch.setattr(tunnel, 'STATE', tmp_path)
    tmp_path.chmod(0o700)
    monkeypatch.setattr(tunnel, 'resolve_snapshot', lambda: next(values))
    monkeypatch.setattr(tunnel, 'voice_idle', lambda: next(idles))
    class Child:
        stopped = False
        def __init__(self, running): self.running = running
        def wait(self, timeout):
            if self.running and not self.stopped:
                raise subprocess.TimeoutExpired('fixture SSH', timeout)
            return 0
        def poll(self): return 0 if self.stopped or not self.running else None
        def terminate(self): self.stopped = True
    starts = []
    monkeypatch.setattr(tunnel, 'start_forward', lambda address: starts.append(address) or Child(address == old['address']))
    assert tunnel.supervise() == 0
    assert starts == [old['address'], new['address']]
    assert json.loads((tmp_path / 'upstream.json').read_text())['pendingIdleReconnect'] is False


@pytest.mark.parametrize('output', ['10 active channels\n10 active calls\n', '100 active channels\n100 active calls\n', 'unknown\n', '0 active channels\n'])
def test_active_or_unknown_call_count_never_allows_rebind(output, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tunnel.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output))
    assert tunnel.voice_idle() is False


def test_exact_zero_call_counts_allow_rebind(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tunnel.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='0 active channels\n0 active calls\n11 calls processed\n'))
    assert tunnel.voice_idle() is True


def test_primary88_reverse_control_reuses_sdk_ssh_without_changing_forward(monkeypatch):
    calls=[]
    monkeypatch.setattr(tunnel.subprocess,'Popen',lambda command:calls.append(command))
    monkeypatch.setenv('USERIO_PRIMARY_CONTROL_REVERSE_SOCKET','/run/agentcall/primary88/control.sock')
    monkeypatch.setattr(tunnel.socket,'gethostname',lambda:'roomhacker-server-88',raising=False)
    tunnel.start_forward('192.168.128.7')
    assert len(calls)==1 and calls[0][-1]==tunnel.SSH_HOST
    assert '127.0.0.1:18086:192.168.128.7:8080' in calls[0]
    assert '/run/agentcall/primary88/control.sock:/run/agentcall/control.sock' in calls[0]
    assert calls[0].count('-R')==1


def test_standby_cannot_export_primary_control(monkeypatch):
    monkeypatch.setenv('USERIO_PRIMARY_CONTROL_REVERSE_SOCKET','/run/agentcall/primary88/control.sock')
    monkeypatch.setattr(tunnel.socket,'gethostname',lambda:'server-44')
    calls=[];monkeypatch.setattr(tunnel.subprocess,'Popen',lambda command:calls.append(command))
    with pytest.raises(ValueError):tunnel.start_forward('192.168.128.7')
    assert not calls
