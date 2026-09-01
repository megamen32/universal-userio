"""Deployment-owned runtime; tokens are resolved locally and never entered through HTTP."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from pathlib import Path

from .adapters import AndroidSmsGatewayClient, HimalayaGmailOutbox, NoticePlaceOutboxClient, NoticePlaceRoute
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


def seed_owner_from_file(store: SQLiteUserIOStore, path: str | Path) -> bool:
    """Apply the private owner seed without returning or logging either secret."""
    seed_path = Path(path)
    if not seed_path.is_file():
        return False
    values: dict[str, str] = {}
    for raw_line in seed_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in {"USERIO_SEED_USERNAME", "USERIO_SEED_PASSWORD"}:
            values[key.strip()] = value.strip()
    username = values.get("USERIO_SEED_USERNAME", "")
    password = values.get("USERIO_SEED_PASSWORD", "")
    if not username or not password:
        raise ValueError("owner seed requires USERIO_SEED_USERNAME and USERIO_SEED_PASSWORD")
    store.seed_owner(username, password)
    return True


def build_service(environment: Mapping[str, str] | None = None) -> UserIOService:
    environment = os.environ if environment is None else environment
    store = SQLiteUserIOStore(_required(environment, "USERIO_DB_PATH"))
    seed_owner_from_file(store, environment.get("USERIO_OWNER_SEED_FILE", ".env.owner-seed"))
    generator = OpenAICompatibleDraftGenerator(
        endpoint=_required(environment, "USERIO_AI_ENDPOINT"), token=_required(environment, "USERIO_AI_TOKEN"), model=_required(environment, "USERIO_AI_MODEL"),
    )
    sms_url, sms_token = environment.get("USERIO_SMS_GATEWAY_URL", "").strip(), environment.get("USERIO_SMS_GATEWAY_TOKEN", "").strip()
    if bool(sms_url) != bool(sms_token):
        raise ValueError("USERIO_SMS_GATEWAY_URL and USERIO_SMS_GATEWAY_TOKEN must be set together")
    gateway = AndroidSmsGatewayClient(sms_url, sms_token) if sms_url else None
    sms_user_id = environment.get("USERIO_SMS_USER_ID", store.default_user_id).strip()
    return UserIOService(
        store, generator, NoticePlaceOutboxClient(routes_from_environment(environment)), sms_gateway=gateway,
        sms_user_id=sms_user_id, sms_route_id=environment.get("USERIO_SMS_ROUTE_ID", "sms").strip() or "sms",
        gmail_outbox=HimalayaGmailOutbox(),
    )


def main() -> None:
    environment = os.environ
    service = build_service(environment)
    token = _required(environment, "USERIO_API_TOKEN")
    server = ThreadingHTTPServer(
        (environment.get("USERIO_HOST", "127.0.0.1"), int(environment.get("USERIO_PORT", "18093"))),
        handler(
            service, token=token, vkid_app_id=environment.get("USERIO_VKID_APP_ID", ""),
            trusted_proxy_token=environment.get("USERIO_TRUSTED_PROXY_TOKEN", ""),
        ),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
