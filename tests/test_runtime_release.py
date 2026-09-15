from __future__ import annotations

import json
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.runtime_release import (
    ReleaseError,
    activate_services,
    build_manifest,
    install_release,
    prepare_rollback,
    restore_release,
    run_canary,
    verify_release,
)
from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.email", "release@example.invalid")
    _git(source, "config", "user.name", "Runtime Release Test")
    files = {
        "pyproject.toml": "[project]\nname='fixture'\n",
        "src/universal_userio/__init__.py": "VERSION = 'new'\n",
        "src/universal_userio/runtime.py": "IDENTITY = 'new'\n",
        "deploy/universal-userio.service": "[Service]\nExecStart=/bin/true\n",
        "deploy/userio-agentcall-ingress.service": "[Service]\nExecStart=/bin/true\n",
    }
    for relative, body in files.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _git(source, "add", "--", *files)
    _git(source, "commit", "-q", "-m", "fixture release")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-q", "-u", "origin", "main")
    return source


def test_manifest_requires_clean_exact_origin_commit_and_safe_tracked_files(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)

    manifest = build_manifest(source)

    assert manifest["commit"] == _git(source, "rev-parse", "HEAD")
    assert manifest["commit"] == _git(source, "rev-parse", "origin/main")
    assert len(manifest["manifest_sha256"]) == 64
    assert [entry["path"] for entry in manifest["entries"]] == [
        "deploy/universal-userio.service",
        "deploy/userio-agentcall-ingress.service",
        "pyproject.toml",
        "src/universal_userio/__init__.py",
        "src/universal_userio/runtime.py",
    ]
    (source / "src/universal_userio/runtime.py").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="clean canonical worktree"):
        build_manifest(source)


def test_manifest_rejects_tracked_symlink(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    (source / "src/universal_userio/link.py").symlink_to("runtime.py")
    _git(source, "add", "src/universal_userio/link.py")
    _git(source, "commit", "-q", "-m", "unsafe link")
    _git(source, "push", "-q", "origin", "main")

    with pytest.raises(ReleaseError, match="symlink"):
        build_manifest(source)


def test_prepare_install_verify_and_restore_are_allowlisted(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    runtime = tmp_path / "runtime"
    unit_root = tmp_path / "units"
    rollback_root = tmp_path / "rollbacks"
    (runtime / "src/universal_userio").mkdir(parents=True)
    unit_root.mkdir()
    (runtime / "src/universal_userio/runtime.py").write_text("IDENTITY = 'old'\n", encoding="utf-8")
    (runtime / ".env.owner-seed").write_text("PRIVATE-SENTINEL\n", encoding="utf-8")
    (unit_root / "universal-userio.service").write_text("old unit\n", encoding="utf-8")
    manifest = build_manifest(source)

    rollback = prepare_rollback(manifest, runtime, unit_root, rollback_root)
    install = install_release(source, manifest, rollback, runtime, unit_root)
    verification = verify_release(source, manifest, runtime, unit_root)

    assert Path(rollback["archive_path"]).is_file()
    assert rollback["restore_argv"][1:3] == ["scripts/runtime_release.py", "restore"]
    assert install["commit"] == manifest["commit"]
    assert verification == {
        "commit": manifest["commit"],
        "manifest_sha256": manifest["manifest_sha256"],
        "verified": True,
    }
    assert (runtime / "src/universal_userio/runtime.py").read_text() == "IDENTITY = 'new'\n"
    assert (runtime / ".env.owner-seed").read_text() == "PRIVATE-SENTINEL\n"
    assert json.loads((runtime / ".userio-release.json").read_text())["commit"] == manifest["commit"]

    restore_release(rollback, runtime, unit_root)
    assert (runtime / "src/universal_userio/runtime.py").read_text() == "IDENTITY = 'old'\n"
    assert (runtime / ".env.owner-seed").read_text() == "PRIVATE-SENTINEL\n"
    assert (unit_root / "universal-userio.service").read_text() == "old unit\n"
    assert not (runtime / ".userio-release.json").exists()


def test_mid_install_failure_restores_before_returning(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    runtime = tmp_path / "runtime"
    unit_root = tmp_path / "units"
    (runtime / "src/universal_userio").mkdir(parents=True)
    unit_root.mkdir()
    original = runtime / "src/universal_userio/runtime.py"
    original.write_text("IDENTITY = 'old'\n", encoding="utf-8")
    state = runtime / ".env.owner-seed"
    state.write_text("PRIVATE-SENTINEL\n", encoding="utf-8")
    manifest = build_manifest(source)
    rollback = prepare_rollback(manifest, runtime, unit_root, tmp_path / "rollbacks")
    events: list[str] = []

    def fail_after_first_write(event: str, target: Path) -> None:
        if event == "after_write":
            events.append(str(target))
            if len(events) == 1:
                raise RuntimeError("injected failure")

    with pytest.raises(ReleaseError, match="restored rollback"):
        install_release(
            source,
            manifest,
            rollback,
            runtime,
            unit_root,
            operation_hook=fail_after_first_write,
        )

    assert Path(rollback["archive_path"]).is_file()
    assert original.read_text() == "IDENTITY = 'old'\n"
    assert state.read_text() == "PRIVATE-SENTINEL\n"


def test_activate_uses_argv_systemctl_in_safe_order() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> None:
        calls.append(argv)

    activate_services(runner=runner)

    assert calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "universal-userio.service"],
        ["systemctl", "restart", "userio-agentcall-ingress.service"],
    ]


class _Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "unused"


class _Outbox:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_reply(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "unexpected"


def test_canary_reads_env_without_echo_and_never_calls_provider(tmp_path: Path) -> None:
    outbox = _Outbox()
    service = UserIOService(
        SQLiteUserIOStore(tmp_path / "userio.sqlite3"), _Generator(), outbox
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(
            service,
            token="canary-secret",
            runtime_identity={
                "schema_version": 1,
                "commit": "a" * 40,
                "manifest_sha256": "b" * 64,
                "verified": True,
            },
        )
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env_file = tmp_path / "userio.env"
    env_file.write_text(
        f"USERIO_API_TOKEN=canary-secret\nUSERIO_PORT={server.server_port}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    try:
        receipt = run_canary(env_file, message_id="phase7-canary-fixed")
    finally:
        server.shutdown()
        server.server_close()

    encoded = json.dumps(receipt)
    assert receipt["accepted"] is True
    assert receipt["draft"] is None
    assert receipt["read_back"] is True
    assert receipt["message_id"] == "phase7-canary-fixed"
    assert outbox.calls == []
    assert "canary-secret" not in encoded

