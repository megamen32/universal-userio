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
