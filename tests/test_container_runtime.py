"""Fast unit/focused integration: bounded supervisor and local RTC transport.

Detects lost child identity, false health, orphan processes and accidental SSH
after consolidation. Expected 8 seconds; emergency limit 80 seconds.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from universal_userio import container_runtime as runtime
from universal_userio.telemost.meetings import Meetings


@pytest.mark.parametrize('processes', [[], [{'name': 'x', 'command': ['true']}],
    [{'name': 'core', 'command': 'shell command'}],
    [{'name': 'core', 'command': ['true']}, {'name': 'core', 'command': ['true']} ]])
def test_invalid_process_configuration_fails_before_start(tmp_path, processes):
    p = tmp_path / 'config.json'; p.write_text(json.dumps({'processes': processes}))
    with pytest.raises(ValueError):
        runtime.load_config(p)


def test_local_rtc_command_preserves_native_client_without_ssh(monkeypatch):
    monkeypatch.setenv('USERIO_TELEMOST_LOCAL', '1')
    assert Meetings(None)._command() == [sys.executable, '-m', 'universal_userio.telemost.client']


def test_health_rejects_missing_worker_even_when_core_is_ready(tmp_path, monkeypatch):
    p = tmp_path / 'status.json'; monkeypatch.setattr(runtime, 'STATUS', p)
    p.write_text(json.dumps({'expected': 2, 'stopping': False,
                            'processes': [{'pid': os.getpid(), 'running': True}]}))
    monkeypatch.setattr(runtime, 'ready', lambda _url: True)
    assert runtime.health() == 1


def test_voice_role_uses_its_own_primary_and_readiness(tmp_path, monkeypatch):
    config = {'primary': 'tts', 'ready_url': 'http://speech/health',
              'processes': [{'name': 'tts', 'command': ['true']}]}
    path = tmp_path / 'voice.json'; path.write_text(json.dumps(config))
    assert runtime.load_config(path)['primary'] == 'tts'
    status = tmp_path / 'status.json'; monkeypatch.setattr(runtime, 'STATUS', status)
    status.write_text(json.dumps({'expected': 1, 'stopping': False,
                                 'processes': [{'pid': os.getpid(), 'running': True}]}))
    urls = []
    monkeypatch.setattr(runtime, 'ready', lambda url: urls.append(url) or True)
    assert runtime.health(config) == 0
    assert urls == ['http://speech/health']
    config['health_sockets'] = [str(tmp_path / 'absent.sock')]
    assert runtime.health(config) == 1


def test_supervisor_keeps_worker_environment_and_stops_owned_group(tmp_path):
    marker = tmp_path / 'marker'
    script = ('import os,pathlib,signal,time;'
              f'p=pathlib.Path({str(marker)!r});'
              'p.write_text(os.environ["EXACT_WORKER_ID"]);'
              'signal.signal(signal.SIGTERM,lambda *_:exit(0));'
              'time.sleep(60)')
    supervisor = runtime.Supervisor({'processes': []}, status=tmp_path / 'status')
    supervisor.start({'name': 'fixture', 'command': [sys.executable, '-c', script],
                      'cwd': str(tmp_path), 'environment': {'EXACT_WORKER_ID': 'existing-identity'}})
    child = supervisor.children['fixture']
    try:
        until = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < until:
            time.sleep(.01)
        assert marker.read_text() == 'existing-identity'
        supervisor.save()
        assert json.loads(supervisor.status.read_text())['processes'][0]['running']
    finally:
        supervisor.close()
    assert child.poll() == 0


def test_restart_storm_is_finite(tmp_path):
    supervisor = runtime.Supervisor({'processes': []}, status=tmp_path / 'status')
    supervisor.attempts['fixture'].extend([time.monotonic()] * 5)
    with pytest.raises(RuntimeError, match='repeatedly failed'):
        supervisor.start({'name': 'fixture', 'command': ['/not/executed']})


def test_adapter_library_imports_without_site_packages_or_daemons():
    source = str(Path(__file__).resolve().parents[1] / 'src')
    r = subprocess.run([sys.executable, '-S', '-c',
        'import sys;sys.path.insert(0,' + repr(source) + ');'
        'from userio_adapter_sdk import Omnichannel,AdapterCapabilities;'
        'from universal_userio import SQLiteUserIOStore,UserIOService;'
        'assert Omnichannel() is not None;print("library-ready")'],
        capture_output=True, text=True, timeout=5)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'library-ready'
