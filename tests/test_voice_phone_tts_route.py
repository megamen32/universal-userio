"""The phone TTS reaches the phone through the configured route.

The S21 tunnel ends on the host that owns the phone. A mesh node has no local
tunnel, so without the configured jump the synthesis fails with "Android TTS
engine failed" and the call falls back to the local engine.
"""
import importlib.util
from pathlib import Path
import sys


PATH = Path(__file__).resolve().parents[1] / 'src/universal_userio/voice/agentcall_realtime_bridge.py'
spec = importlib.util.spec_from_file_location('phone_tts_bridge', PATH)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


def test_route_is_empty_without_a_configured_jump(monkeypatch):
    monkeypatch.delenv('AGENTCALL_PHONE_TTS_SSH_CONFIG', raising=False)
    monkeypatch.delenv('AGENTCALL_PHONE_TTS_SSH_JUMP', raising=False)
    assert bridge._phone_ssh_route() == []


def test_route_carries_the_configured_file_and_jump(monkeypatch):
    monkeypatch.setenv('AGENTCALL_PHONE_TTS_SSH_CONFIG', '/etc/agentcall-tts-ssh-config')
    monkeypatch.setenv('AGENTCALL_PHONE_TTS_SSH_JUMP', 'voice-sdk-host')
    assert bridge._phone_ssh_route() == [
        '-F', '/etc/agentcall-tts-ssh-config', '-J', 'voice-sdk-host']


def test_synthesis_command_carries_the_route(monkeypatch):
    monkeypatch.setenv('AGENTCALL_PHONE_TTS_SSH_CONFIG', '/etc/agentcall-tts-ssh-config')
    monkeypatch.setenv('AGENTCALL_PHONE_TTS_SSH_JUMP', 'voice-sdk-host')
    seen = {}

    def fake_run(args, **kwargs):
        if args[0] == '/usr/local/bin/s21-lease':
            if 'acquire' in args:
                return type('Lease', (), {'returncode': 0, 'stdout': '{"lease_id": "x"}'})()
            return type('Lease', (), {'returncode': 0, 'stdout': ''})()
        seen['argv'] = list(args)
        return type('Run', (), {'returncode': 0, 'stdout': ''})()

    monkeypatch.setattr(bridge.subprocess, 'run', fake_run)
    try:
        bridge.synthesize_phone_android('проверка')
    except Exception:
        pass
    argv = seen.get('argv')
    assert argv, 'the synthesis did not reach the ssh command'
    assert '-F' in argv and '/etc/agentcall-tts-ssh-config' in argv
    assert argv[argv.index('-J') + 1] == 'voice-sdk-host'
    assert 'phone@127.0.0.1' in argv
