import importlib.util
from pathlib import Path
from unittest.mock import Mock

MODULE = Path(__file__).parents[1] / 'src/universal_userio/voice/agentcall_phone_tts.py'
spec = importlib.util.spec_from_file_location('phone_tts_transport', MODULE)
phone_tts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone_tts)


def command_for(monkeypatch, jump):
    if jump is None:
        monkeypatch.delenv('AGENTCALL_PHONE_TTS_SSH_JUMP', raising=False)
    else:
        monkeypatch.setenv('AGENTCALL_PHONE_TTS_SSH_JUMP', jump)
    process = Mock()
    popen = Mock(return_value=process)
    monkeypatch.setattr(phone_tts.subprocess, 'Popen', popen)
    worker = phone_tts.WarmPhoneTts()
    monkeypatch.setattr(worker, '_event', lambda _: {'event': 'ready', 'run_tag': worker._tag})
    worker._ensure()
    return popen.call_args.args[0]


def test_remote_gateway_uses_operator_selected_jump(monkeypatch):
    command = command_for(monkeypatch, 'roomhacker@192.168.2.100')
    assert command[command.index('-J') + 1] == 'roomhacker@192.168.2.100'
    assert 'phone@127.0.0.1' in command
    assert command[command.index('-p') + 1] == '22220'


def test_canonical_gateway_keeps_direct_loopback(monkeypatch):
    command = command_for(monkeypatch, None)
    assert '-J' not in command
    assert 'phone@127.0.0.1' in command


def test_service_can_use_its_scoped_verified_ssh_config(monkeypatch):
    monkeypatch.setenv('AGENTCALL_PHONE_TTS_SSH_CONFIG', '/etc/agentcall-tts-ssh-config')
    command = command_for(monkeypatch, 'voice-sdk-host')
    assert command[command.index('-F') + 1] == '/etc/agentcall-tts-ssh-config'
    assert command[command.index('-J') + 1] == 'voice-sdk-host'
    assert 'StrictHostKeyChecking=no' not in command
