from __future__ import annotations

import json

from universal_userio.runtime import routes_from_environment


def test_route_registry_resolves_only_named_environment_token() -> None:
    environment = {
        "USERIO_ROUTES_JSON": json.dumps({"telegram": {"event_url": "http://noticeplace", "token_env": "NOTICEPLACE_TELEGRAM", "project": "userio"}}),
        "NOTICEPLACE_TELEGRAM": "scoped-secret",
    }

    route = routes_from_environment(environment)["telegram"]

    assert route.event_url == "http://noticeplace"
    assert route.token == "scoped-secret"
    assert route.project == "userio"
