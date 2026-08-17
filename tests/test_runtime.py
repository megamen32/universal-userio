from __future__ import annotations

import json

from universal_userio.runtime import build_service, routes_from_environment


def test_route_registry_resolves_only_named_environment_token() -> None:
    environment = {
        "USERIO_ROUTES_JSON": json.dumps({"telegram": {"event_url": "http://noticeplace", "token_env": "NOTICEPLACE_TELEGRAM", "project": "userio"}}),
        "NOTICEPLACE_TELEGRAM": "scoped-secret",
    }

    route = routes_from_environment(environment)["telegram"]

    assert route.event_url == "http://noticeplace"
    assert route.token == "scoped-secret"
    assert route.project == "userio"


def test_runtime_builds_service_from_deployment_owned_configuration(tmp_path) -> None:
    environment = {
        "USERIO_DB_PATH": str(tmp_path / "userio.sqlite3"),
        "USERIO_AI_ENDPOINT": "http://127.0.0.1:4000/v1",
        "USERIO_AI_TOKEN": "ai-secret",
        "USERIO_AI_MODEL": "business-model",
        "USERIO_ROUTES_JSON": json.dumps({"telegram": {"event_url": "http://noticeplace", "token_env": "NOTICEPLACE_TELEGRAM"}}),
        "NOTICEPLACE_TELEGRAM": "scoped-secret",
    }

    service = build_service(environment)

    assert service.__class__.__name__ == "UserIOService"
