from __future__ import annotations

import json

import pytest

from universal_userio.runtime import build_service, load_runtime_identity


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
        "USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT_ID": "540308572",
        "USERIO_DRAFT_NOTIFY_TELEGRAM_ACCOUNT_REF": "telegram:8810909089",
        "USERIO_DRAFT_NOTIFY_DELAY_SECONDS": "7",
    }
    service = build_service(environment)
    assert service.draft_notifier is not None
    assert service.draft_notifier.__class__.__name__ == "TelegramDraftApprovalNotifier"
    assert service.draft_notification_delay_seconds == 7.0
