"""Deployment-owned runtime; tokens are resolved locally and never entered through HTTP."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from http.server import ThreadingHTTPServer

from .adapters import NoticePlaceOutboxClient, NoticePlaceRoute
from .ai import OpenAICompatibleDraftGenerator
from .http_api import handler
from .service import UserIOService
from .store import SQLiteUserIOStore


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def routes_from_environment(environment: Mapping[str, str]) -> dict[str, NoticePlaceRoute]:
    raw = _required(environment, "USERIO_ROUTES_JSON")
    try:
        registry = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("USERIO_ROUTES_JSON must be valid JSON") from error
    if not isinstance(registry, dict) or not registry:
        raise ValueError("USERIO_ROUTES_JSON must contain routes")
    routes = {}
    for route_id, config in registry.items():
        if not isinstance(config, dict):
            raise ValueError(f"route {route_id} must be an object")
        token_env = str(config.get("token_env") or "")
        routes[str(route_id)] = NoticePlaceRoute(
            event_url=str(config.get("event_url") or ""), token=_required(environment, token_env),
            project=str(config.get("project") or "userio"), recipient=str(config.get("recipient") or "userio"),
            severity=str(config.get("severity") or "notice"),
        )
    return routes


def build_service(environment: Mapping[str, str] | None = None) -> UserIOService:
    environment = os.environ if environment is None else environment
    store = SQLiteUserIOStore(_required(environment, "USERIO_DB_PATH"))
    generator = OpenAICompatibleDraftGenerator(
        endpoint=_required(environment, "USERIO_AI_ENDPOINT"), token=_required(environment, "USERIO_AI_TOKEN"), model=_required(environment, "USERIO_AI_MODEL"),
    )
    return UserIOService(store, generator, NoticePlaceOutboxClient(routes_from_environment(environment)))


def main() -> None:
    environment = os.environ
    service = build_service(environment)
    token = _required(environment, "USERIO_API_TOKEN")
    server = ThreadingHTTPServer(
        (environment.get("USERIO_HOST", "127.0.0.1"), int(environment.get("USERIO_PORT", "18093"))),
        handler(service, token=token, vkid_app_id=environment.get("USERIO_VKID_APP_ID", "")),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
