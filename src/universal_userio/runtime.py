"""Deployment-owned runtime; tokens are resolved locally and never entered through HTTP."""

from __future__ import annotations

import os
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from pathlib import Path

from .adapters import ChatGPTWebOutbox, DirectProviderOutbox, HimalayaGmailOutbox
from .channels.sms_gateway import AndroidSmsGatewayClient
from .ai import OpenAICompatibleDraftGenerator
from .channels.live_telegram import live_telegram_outbox_from_env
from .draft_notifications import TelegramDraftApprovalNotifier
from .http_api import handler
from .service import UserIOService
from .store import SQLiteUserIOStore


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value



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


def telegram_outbox_from_env(environment: Mapping[str, str]):
    """Prefer the telegram-qr connector HTTP outbox; fall back to in-process Telethon."""
    qr_url = environment.get("USERIO_TELEGRAM_QR_URL", "").strip()
    if qr_url:
        from .adapters import TelegramQrHttpOutbox

        token = environment.get("USERIO_TELEGRAM_QR_TOKEN", "").strip() or environment.get("USERIO_API_TOKEN", "")
        return TelegramQrHttpOutbox(qr_url, token)
    return live_telegram_outbox_from_env(environment)


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
    telegram_outbox = telegram_outbox_from_env(environment)
    notify_chat = environment.get("USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT", "").strip()
    notify_chat_id = environment.get("USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    notify_account_ref = environment.get("USERIO_DRAFT_NOTIFY_TELEGRAM_ACCOUNT_REF", "").strip()
    notify_values = (notify_chat, notify_chat_id, notify_account_ref)
    if any(notify_values) and not all(notify_values):
        raise ValueError(
            "USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT, USERIO_DRAFT_NOTIFY_TELEGRAM_CHAT_ID and "
            "USERIO_DRAFT_NOTIFY_TELEGRAM_ACCOUNT_REF must be set together"
        )
    draft_notifier = None
    if all(notify_values):
        draft_notifier = TelegramDraftApprovalNotifier(
            telegram_outbox, owner_user_id=store.owner().user_id,
            chat=notify_chat, chat_id=notify_chat_id, account_ref=notify_account_ref,
        )
    return UserIOService(
        store, generator, DirectProviderOutbox(), sms_gateway=gateway,
        sms_user_id=sms_user_id, sms_route_id=environment.get("USERIO_SMS_ROUTE_ID", "sms").strip() or "sms",
        gmail_outbox=HimalayaGmailOutbox(),
        chatgpt_outbox=ChatGPTWebOutbox(),
        telegram_outbox=telegram_outbox, draft_notifier=draft_notifier,
        draft_notification_delay_seconds=float(
            environment.get("USERIO_DRAFT_NOTIFY_DELAY_SECONDS", "5") or "5"
        ),
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
