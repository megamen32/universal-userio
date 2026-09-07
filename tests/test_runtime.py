from __future__ import annotations

from universal_userio.runtime import build_service


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
