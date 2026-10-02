"""Telegram session provisioning via QR login — two proven originals.

1. Interactive terminal QR (original: overpod/mcp-telegram, now
   mcp-telegram/mcp-telegram — ``npx @overpod/mcp-telegram login``):
   :func:`login_via_qr` renders the ``tg://login`` QR in the terminal, the
   owner scans it with the phone (Settings > Devices > Link Desktop Device),
   an optional 2FA password comes from ``USERIO_TELEGRAM_2FA_PASSWORD`` /
   ``TELEGRAM_2FA_PASSWORD`` (answered locally via SRP, never persisted), and
   the session is stored with owner-only permissions.

2. Programmatic mint from an authorized client (original:
   TelegramAuto/TGC ``qr_session_login.py``): :func:`create_independent_session_via_qr`
   calls ``auth.acceptLoginToken`` from an already-authorized client — the
   RECAPTCHA-bypass path that produces a fresh independent auth key without
   camera, SMS or reCAPTCHA (no AuthKeyDuplicated).

Wire contract (telegram-source-first gate, 2026-09-03): TL schema
``auth.exportLoginToken#b7e085fe`` / ``auth.acceptLoginToken#e894ad4d`` /
``auth.loginTokenSuccess#390d5c5e`` (td telegram_api.tl:2331/2333/1366);
official client tweb ``src/lib/mtproto/schema.ts``; installed layer: telethon
``AuthMethods.qr_login``, ``AcceptLoginTokenRequest(token)``.

CLI (overpod UX):
    python -m universal_userio.channels.telegram_login [session_path]
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient as PlainTelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.auth import AcceptLoginTokenRequest

try:  # opentele2 owns the compatible Telethon runtime when installed
    from opentele2.api import API, APIData
    from opentele2.tl import TelegramClient as OpenteleClient
except ImportError:  # pragma: no cover - plain Telethon fallback
    API = None  # type: ignore[assignment]
    APIData = None  # type: ignore[assignment]
    OpenteleClient = None  # type: ignore[assignment]

QR_POLL_SECONDS = 20
QR_TOKEN_LIFETIME_HINT = "the QR refreshes automatically; keep the phone ready"


@dataclass(slots=True)
class QrLoginResult:
    ok: bool
    new_client: Any = None
    reason: str | None = None
    new_session_string: str | None = None
    session_path: Path | None = None


def session_to_string(session: Any) -> str:
    """Serialize any Telethon session into a StringSession payload."""

    string_session = StringSession()
    string_session.set_dc(session.dc_id, session.server_address, session.port)
    string_session.auth_key = session.auth_key
    return string_session.save()


def _resolved_credentials(
    api_id: int | None, api_hash: str | None
) -> tuple[int | None, str | None]:
    env = os.environ

    def pick(*names: str) -> str | None:
        for name in names:
            value = env.get(name)
            if value:
                return value
        return None

    resolved_id = api_id or pick("USERIO_TELEGRAM_API_ID", "TG_API_ID")
    resolved_hash = api_hash or pick("USERIO_TELEGRAM_API_HASH", "TG_API_HASH")
    return (int(resolved_id) if resolved_id else None), resolved_hash


def _new_client(
    session_value: Any,
    *,
    api: Any = None,
    api_id: int | None = None,
    api_hash: str | None = None,
    proxy: dict | None = None,
) -> Any:
    if api is not None and OpenteleClient is not None:
        return OpenteleClient(session_value, api=api, proxy=proxy)
    if api is None and APIData is not None and api_id is None:
        return OpenteleClient(session_value, api=API.TelegramAndroid, proxy=proxy)
    if not api_id or not api_hash:
        raise ValueError(
            "QR login needs api credentials: pass api_id/api_hash"
            " (or USERIO_TELEGRAM_API_ID/HASH, TG_API_ID/HASH)"
            " or install opentele2"
        )
    return PlainTelegramClient(session_value, api_id, api_hash, proxy=proxy)


def _render_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError:  # graceful: print the raw login URL
        print(f"QR library not installed; open/convert this login URL manually:\n{url}")
        return
    code = qrcode.QRCode()
    code.add_data(url)
    code.print_ascii()


async def login_via_qr(
    *,
    api_id: int | None = None,
    api_hash: str | None = None,
    api: Any = None,
    proxy: dict | None = None,
    session_path: Path | str | None = None,
    password: str | None = None,
    timeout_seconds: int = 180,
    renderer: Callable[[str], None] | None = None,
    client_factory: Callable[[Any], Any] | None = None,
) -> QrLoginResult:
    """Interactive QR login (overpod/mcp-telegram style).

    Renders the QR in the terminal (``renderer`` injectable for tests), waits
    for the phone scan, answers the 2FA SRP challenge when a password is
    configured, and persists the session at ``session_path`` (default
    ``~/.userio/telegram.session``, ``USERIO_TELEGRAM_SESSION_PATH`` override)
    with owner-only permissions.
    """

    env = os.environ
    api_id, api_hash = _resolved_credentials(api_id, api_hash)
    if password is None:
        password = env.get("USERIO_TELEGRAM_2FA_PASSWORD") or env.get(
            "TELEGRAM_2FA_PASSWORD"
        )
    if session_path is None:
        session_path = Path(
            env.get("USERIO_TELEGRAM_SESSION_PATH")
            or Path.home() / ".userio" / "telegram.session"
        )
    session_path = Path(session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    factory = client_factory or (
        lambda value: _new_client(
            value, api=api, api_id=api_id, api_hash=api_hash, proxy=proxy
        )
    )
    render = renderer or _render_qr

    client = factory(str(session_path))
    try:
        await client.connect()
        print(f"Scan the QR with Telegram > Settings > Devices > Link Desktop Device ({QR_TOKEN_LIFETIME_HINT}).")
        qr = await client.qr_login()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            render(str(qr.url))
            try:
                await asyncio.wait_for(qr.wait(), timeout=QR_POLL_SECONDS)
                break
            except SessionPasswordNeededError:
                if not password:
                    raise
                await client.sign_in(password=password)
                break
            except asyncio.TimeoutError:
                if loop.time() >= deadline:
                    raise asyncio.TimeoutError("QR login timed out") from None
                qr = await qr.recreate()
        if not await client.is_user_authorized():
            raise RuntimeError("QR flow completed but the session is not authorized")
        client.session.save() if hasattr(client.session, "save") else None
        try:
            session_path.chmod(0o600)
        except OSError:  # pragma: no cover - platform without chmod
            pass
        return QrLoginResult(
            ok=True,
            new_client=client,
            new_session_string=session_to_string(client.session),
            session_path=session_path,
        )
    except SessionPasswordNeededError:
        reason = "twofa_required"
    except asyncio.TimeoutError:
        reason = "qr_timeout"
    except Exception as exc:  # noqa: BLE001 - stable result boundary for callers
        reason = str(exc)

    if client is not None and client.is_connected():
        await client.disconnect()
    return QrLoginResult(ok=False, reason=reason)


async def create_independent_session_via_qr(
    *,
    authorized_client: Any,
    new_client_api: Any = None,
    api_id: int | None = None,
    api_hash: str | None = None,
    proxy_cfg: dict | None = None,
    accept_timeout_seconds: int = 25,
    session_path: Path | None = None,
    client_factory: Callable[[Any], Any] | None = None,
) -> QrLoginResult:
    """Create a connected client with a fresh independent auth key (TGC original).

    ``authorized_client`` must already be logged in on the target account; it
    programmatically approves the new session via ``auth.acceptLoginToken``
    (no camera, no SMS, no reCAPTCHA wall).  ``new_client_api`` is an
    opentele2 ``APIData``; plain Telethon needs ``api_id``/``api_hash``.
    The TGC original wraps client creation in ``strict_telethon`` lock scopes;
    that guard belongs to the TGC runtime, not this library.
    """

    new_client: Any = None
    try:
        if client_factory is not None:
            new_client = client_factory(
                str(session_path) if session_path is not None else StringSession()
            )
        else:
            new_client = _new_client(
                str(session_path) if session_path is not None else StringSession(),
                api=new_client_api,
                api_id=api_id,
                api_hash=api_hash,
                proxy=proxy_cfg,
            )
        new_client.session.set_dc(
            authorized_client.session.dc_id,
            authorized_client.session.server_address,
            authorized_client.session.port,
        )
        await new_client.connect()
        qr = await new_client.qr_login()
        await authorized_client(AcceptLoginTokenRequest(token=qr.token))
        await asyncio.wait_for(qr.wait(), timeout=accept_timeout_seconds)
        if session_path is not None:
            new_client.session.save()
        return QrLoginResult(
            ok=True,
            new_client=new_client,
            new_session_string=session_to_string(new_client.session),
            session_path=session_path,
        )
    except SessionPasswordNeededError:
        reason = "twofa_required"
    except asyncio.TimeoutError:
        reason = "qr_timeout"
    except Exception as exc:  # noqa: BLE001 - stable result boundary for callers
        reason = str(exc)

    if new_client is not None and new_client.is_connected():
        await new_client.disconnect()
    return QrLoginResult(ok=False, reason=reason)


def _cli() -> int:
    session_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = asyncio.run(login_via_qr(session_path=session_arg))
    if not result.ok:
        print(f"QR login failed: {result.reason}")
        return 1
    print(f"Session saved: {result.session_path}")
    print(
        "Use it with: USERIO_TELEGRAM_SESSION="
        f"{result.session_path} (or export the StringSession into your env store)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual operator entry point
    raise SystemExit(_cli())
