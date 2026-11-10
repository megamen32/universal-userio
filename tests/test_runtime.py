from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from universal_userio.runtime import SummaryCacheMaintenance, build_service, load_runtime_identity


def test_http_server_queues_connection_bursts() -> None:
    source = Path(__file__).parents[1] / "src" / "universal_userio" / "runtime.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    server = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.ClassDef) and node.name == "UserIOHTTPServer"
    )
    backlog = next(
        node.value
        for node in server.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "request_queue_size" for target in node.targets)
    )
    assert isinstance(backlog, ast.Constant)
    assert backlog.value == 128


def test_summary_cache_maintenance_runs_only_when_due() -> None:
    now = [100.0]
    calls: list[float] = []
    maintenance = SummaryCacheMaintenance(
        lambda: calls.append(now[0]) or 3,
        interval_seconds=60,
        clock=lambda: now[0],
    )

    assert maintenance.run_if_due() == 0
    now[0] = 160.0
    assert maintenance.run_if_due() == 3
    assert maintenance.run_if_due() == 0
    assert calls == [160.0]


def test_runtime_identity_loads_strict_verified_release_file(tmp_path) -> None:
    release_file = tmp_path / ".userio-release.json"
    release_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "commit": "a" * 40,
                "manifest_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    release_file.chmod(0o644)

    identity = load_runtime_identity(release_file)

    assert dict(identity) == {
        "schema_version": 1,
        "commit": "a" * 40,
        "manifest_sha256": "b" * 64,
        "verified": True,
    }
    with pytest.raises(TypeError):
        identity["verified"] = False


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"schema_version": 1, "commit": "a" * 40, "manifest_sha256": "b" * 64, "secret": "sentinel"}),
        json.dumps({"schema_version": "1", "commit": "a" * 40, "manifest_sha256": "b" * 64}),
        json.dumps({"schema_version": 1, "commit": "A" * 40, "manifest_sha256": "b" * 64}),
        json.dumps({"schema_version": 1, "commit": "a" * 40, "manifest_sha256": "short"}),
    ],
)
def test_runtime_identity_rejects_malformed_or_extra_data(tmp_path, payload: str) -> None:
    release_file = tmp_path / ".userio-release.json"
    release_file.write_text(payload, encoding="utf-8")
    release_file.chmod(0o644)

    assert dict(load_runtime_identity(release_file)) == {
        "schema_version": 1,
        "commit": None,
        "manifest_sha256": None,
        "verified": False,
    }


def test_runtime_identity_missing_or_wrong_mode_is_unverified(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    assert load_runtime_identity(missing)["verified"] is False
    release_file = tmp_path / ".userio-release.json"
    release_file.write_text(
        json.dumps({"schema_version": 1, "commit": "a" * 40, "manifest_sha256": "b" * 64}),
        encoding="utf-8",
    )
    release_file.chmod(0o666)
    assert load_runtime_identity(release_file)["verified"] is False
    release_file.chmod(0o644)
    symlink = tmp_path / "release-link.json"
    symlink.symlink_to(release_file)
    assert load_runtime_identity(symlink)["verified"] is False


def test_runtime_builds_service_from_deployment_owned_configuration(tmp_path) -> None:
    environment = {
        "USERIO_DB_PATH": str(tmp_path / "userio.sqlite3"),
        "USERIO_AI_ENDPOINT": "http://127.0.0.1:4000/v1",
        "USERIO_AI_TOKEN": "ai-secret",
        "USERIO_AI_MODEL": "business-model",
    }

    service = build_service(environment)

    assert service.__class__.__name__ == "UserIOService"



def test_runtime_send_policy_defaults_enabled(tmp_path) -> None:
    environment = {
        "USERIO_DB_PATH": str(tmp_path / "userio.sqlite3"),
        "USERIO_AI_ENDPOINT": "http://127.0.0.1:4000/v1",
        "USERIO_AI_TOKEN": "ai-secret",
        "USERIO_AI_MODEL": "business-model",
    }
    service = build_service(environment)
    assert service._store.send_enabled() is True


def test_runtime_configures_owner_draft_telegram_notifier(tmp_path) -> None:
    environment = {
        "USERIO_DB_PATH": str(tmp_path / "userio.sqlite3"),
        "USERIO_AI_ENDPOINT": "http://127.0.0.1:4000/v1",
        "USERIO_AI_TOKEN": "ai-secret",
        "USERIO_AI_MODEL": "business-model",
        "USERIO_TELEGRAM_QR_URL": "http://127.0.0.1:18095",
        "USERIO_TELEGRAM_QR_TOKEN": "bridge-token",
        "USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT": "Никита Розанов",
        "USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT_ID": "900000101",
        "USERIO_DRAFT_NOTIFY_TELEGRAM_ACCOUNT_REF": "telegram:900000103",
        "USERIO_DRAFT_NOTIFY_DELAY_SECONDS": "7",
    }
    service = build_service(environment)
    assert service.draft_notifier is not None
    assert service.draft_notifier.__class__.__name__ == "TelegramDraftApprovalNotifier"
    assert service.draft_notification_delay_seconds == 7.0
