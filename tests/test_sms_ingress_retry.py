"""Fast unit: transient poll recovery, expected1/max10s; no network/SMS."""
import pytest
from universal_userio import sms_ingress as module


def configure(monkeypatch):
    monkeypatch.setenv('USERIO_API_TOKEN', 'unit-only')
    monkeypatch.setenv('USERIO_SMS_GATEWAY_TOKEN', 'unit-only')
    monkeypatch.setenv('USERIO_SMS_GATEWAY_URL', 'http://unit-only')
    monkeypatch.setattr(module, 'AndroidSmsGatewayClient', lambda *args: object())
    monkeypatch.setattr(module, 'UserIOIngressClient', lambda *args: object())
    monkeypatch.setattr(module.signal, 'signal', lambda *args: None)


def test_continuous_poll_recovers_without_process_restart(monkeypatch):
    configure(monkeypatch)
    class Stop:
        waits = []
        def is_set(self): return len(self.waits) >= 2
        def wait(self, seconds): self.waits.append(seconds)
        def set(self): pass
    stop = Stop()
    monkeypatch.setattr(module.threading, 'Event', lambda: stop)
    calls = []
    def poll(*args):
        calls.append(1)
        if len(calls) == 1: raise TimeoutError('temporary provider failure')
        return 1
    monkeypatch.setattr(module, 'poll_once', poll)
    assert module.main([]) == 0
    assert len(calls) == 2 and stop.waits == [5.0, 5.0]


def test_once_retains_diagnostic_failure(monkeypatch):
    configure(monkeypatch)
    def poll(*args): raise TimeoutError('temporary provider failure')
    monkeypatch.setattr(module, 'poll_once', poll)
    with pytest.raises(TimeoutError): module.main(['--once'])
