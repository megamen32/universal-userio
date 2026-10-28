"""Incoming Matrix receipts use the fixed answer bound, never an unbounded bypass."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def idle_guard(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/container_release.py'
    spec = importlib.util.spec_from_file_location('incoming_idle_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ASTERISK_HOSTS', [])
    monkeypatch.setattr(module, 'command', lambda *a, **kw: None)
    monkeypatch.setattr(module, 'ssh_args', lambda *a, **kw: [])
    monkeypatch.setattr(module, 'Path', lambda p: tmp_path if p == '/var/lib/universal-userio' else Path(p))
    monkeypatch.setattr(module.time, 'time', lambda: 10000)
    return module, tmp_path


def receipt(root, channel, values):
    path = root / channel / 'test-call' / 'call.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(values))
    return path


def test_expired_incoming_fixed_bound_is_idle_without_rewriting_receipt(idle_guard):
    guard, root = idle_guard
    path = receipt(root, 'matrix-calls', {'direction': 'incoming', 'status': 'unknown', 'created_at': 1000})
    before = path.read_bytes()
    guard.assert_idle()
    assert path.read_bytes() == before


@pytest.mark.parametrize('channel,values', [
    ('matrix-calls', {'direction': 'incoming', 'status': 'accepted', 'created_at': 9900}),
    ('matrix-calls', {'direction': 'incoming', 'status': 'unknown', 'created_at': 9900}),
    ('matrix-calls', {'direction': 'incoming', 'status': 'unknown', 'created_at': 10001}),
    ('matrix-calls', {'direction': 'outgoing', 'status': 'unknown', 'created_at': 1000}),
    ('telegram-calls', {'direction': 'incoming', 'status': 'unknown', 'created_at': 1000}),
    ('matrix-calls', {'direction': 'incoming', 'status': 'accepted', 'created_at': 1000, 'max_seconds': 0}),
    ('matrix-calls', {'direction': 'incoming', 'status': 'accepted', 'created_at': 1000, 'max_seconds': None}),
])
def test_missing_or_unsafe_bound_remains_fail_closed(idle_guard, channel, values):
    guard, root = idle_guard
    receipt(root, channel, values)
    with pytest.raises(RuntimeError):
        guard.assert_idle()
