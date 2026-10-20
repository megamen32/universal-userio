"""Passwordless login: one-time email codes and the official Telegram Login Widget.

Both channels end in the same place: a verified channel identity is bound to
exactly one UserIO user and the caller receives that user's principal.

- Email: a six-digit code is generated, hashed with PBKDF2 and delivered by
  the configured service account. Only the hash is stored, the code is
  single-use, short-lived and attempt-limited.
- Telegram: Telegram itself performs the confirmation through its Login
  Widget; the backend only verifies the signature the widget returns against
  the bot token (HMAC-SHA256 over the sorted data check string).

Every answer looks the same for known and unknown destinations, so neither
endpoint can be used to enumerate registered addresses.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .contracts import UserPrincipal
from .store import SQLiteUserIOStore

LOG = logging.getLogger(__name__)

CHANNEL_EMAIL = "email"
CHANNEL_TELEGRAM = "telegram"

CODE_DIGITS = 6
CODE_ITERATIONS = 120_000
DEFAULT_TTL_SECONDS = 600
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_REQUESTS_PER_DESTINATION = 3
DEFAULT_DESTINATION_WINDOW_SECONDS = 900
DEFAULT_REQUESTS_PER_IP = 10
DEFAULT_IP_WINDOW_SECONDS = 3600
DEFAULT_TELEGRAM_AUTH_MAX_AGE = 86400

_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")
_CODE = re.compile(r"\d{%d}" % CODE_DIGITS)


def normalize_email(value: str) -> str:
    """Return a comparable address, or an empty string for anything unusable."""

    address = str(value or "").strip().lower()
    if len(address) > 254 or not _EMAIL.fullmatch(address):
        return ""
    return address


def hash_code(code: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt, CODE_ITERATIONS)


def verify_code(code: str, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(hash_code(code, salt), expected)


def verify_telegram_widget(
    payload: Mapping[str, str], bot_token: str, *, max_age: int = DEFAULT_TELEGRAM_AUTH_MAX_AGE,
    now: float | None = None,
) -> dict[str, str] | None:
    """Verify a Telegram Login Widget answer and return the signed identity.

    The widget signs the sorted, newline separated data check string with a
    key derived from the bot token. A wrong signature, a missing field or a
    stale ``auth_date`` all fail closed.
    """

    if not bot_token:
        return None
    fields = {str(key): str(value) for key, value in payload.items() if str(value)}
    signature = fields.pop("hash", "")
    telegram_id = fields.get("id", "")
    auth_date = fields.get("auth_date", "")
    if not signature or not telegram_id or not auth_date:
        return None
    try:
        signed_at = int(auth_date)
    except ValueError:
        return None
    moment = time.time() if now is None else now
    if moment - signed_at > max_age or signed_at - moment > 60:
        return None
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return {
        "id": telegram_id,
        "first_name": fields.get("first_name", ""),
        "username": fields.get("username", ""),
        "photo_url": fields.get("photo_url", ""),
    }


def send_login_code_email(*, host: str, port: int, user: str, password: str, sender: str,
                           recipient: str, code: str, ttl_seconds: int) -> None:
    """Deliver one login code through the configured service account."""

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Your UserIO login code"
    message.set_content(
        f"Your UserIO login code is {code}.\n\n"
        f"It stays valid for {max(1, int(ttl_seconds) // 60)} minutes and works once.\n"
        "If you did not ask for it, ignore this message and nothing happens.\n"
    )
    with smtplib.SMTP(host, port, timeout=30) as client:
        client.ehlo()
        client.starttls()
        client.ehlo()
        client.login(user, password)
        client.send_message(message)


@dataclass(frozen=True)
class LoginCodeSettings:
    """Runtime configuration of the passwordless login flow."""

    enabled: bool = False
    registration_enabled: bool = True
    code_ttl_seconds: int = DEFAULT_TTL_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    requests_per_destination: int = DEFAULT_REQUESTS_PER_DESTINATION
    destination_window_seconds: int = DEFAULT_DESTINATION_WINDOW_SECONDS
    requests_per_ip: int = DEFAULT_REQUESTS_PER_IP
    ip_window_seconds: int = DEFAULT_IP_WINDOW_SECONDS
    telegram_auth_max_age: int = DEFAULT_TELEGRAM_AUTH_MAX_AGE
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    telegram_bot_token: str = ""

    @property
    def email_enabled(self) -> bool:
        return bool(self.enabled and self.smtp_host and self.smtp_user
                    and self.smtp_password and self.from_address)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.enabled and self.telegram_bot_token)

    @property
    def telegram_bot_id(self) -> str:
        return self.telegram_bot_token.partition(":")[0] if self.telegram_bot_token else ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LoginCodeSettings":
        source: Mapping[str, str] = os.environ if env is None else env

        def text(*names: str) -> str:
            for name in names:
                value = str(source.get(name) or "").strip()
                if value:
                    return value
            return ""

        def secret(name: str, file_name: str) -> str:
            value = text(name)
            if value:
                return value
            path = text(file_name)
            if not path:
                return ""
            try:
                return Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                LOG.warning("login code secret file is unreadable: %s", path)
                return ""

        def number(*names: str, default: int) -> int:
            raw = text(*names)
            try:
                value = int(raw)
            except ValueError:
                return default
            return value if value > 0 else default

        def flag(*names: str, default: bool) -> bool:
            raw = text(*names)
            if not raw:
                return default
            return raw.lower() in {"1", "true", "yes", "on"}

        return cls(
            enabled=flag("USERIO_LOGIN_CODE_ENABLED", default=False),
            registration_enabled=flag("USERIO_LOGIN_CODE_REGISTRATION", default=True),
            code_ttl_seconds=number("USERIO_LOGIN_CODE_TTL_SECONDS", default=DEFAULT_TTL_SECONDS),
            max_attempts=number("USERIO_LOGIN_CODE_MAX_ATTEMPTS", default=DEFAULT_MAX_ATTEMPTS),
            requests_per_destination=number(
                "USERIO_LOGIN_CODE_REQUESTS_PER_DESTINATION", default=DEFAULT_REQUESTS_PER_DESTINATION),
            destination_window_seconds=number(
                "USERIO_LOGIN_CODE_DESTINATION_WINDOW_SECONDS",
                default=DEFAULT_DESTINATION_WINDOW_SECONDS),
            requests_per_ip=number("USERIO_LOGIN_CODE_REQUESTS_PER_IP", default=DEFAULT_REQUESTS_PER_IP),
            ip_window_seconds=number("USERIO_LOGIN_CODE_IP_WINDOW_SECONDS", default=DEFAULT_IP_WINDOW_SECONDS),
            telegram_auth_max_age=number(
                "USERIO_LOGIN_CODE_TELEGRAM_AUTH_MAX_AGE", default=DEFAULT_TELEGRAM_AUTH_MAX_AGE),
            smtp_host=text("USERIO_LOGIN_CODE_SMTP_HOST"),
            smtp_port=number("USERIO_LOGIN_CODE_SMTP_PORT", default=587),
            smtp_user=text("USERIO_LOGIN_CODE_SMTP_USER"),
            smtp_password=secret("USERIO_LOGIN_CODE_SMTP_PASSWORD", "USERIO_LOGIN_CODE_SMTP_PASSWORD_FILE"),
            from_address=text("USERIO_LOGIN_CODE_FROM"),
            telegram_bot_token=secret(
                "USERIO_LOGIN_CODE_TELEGRAM_BOT_TOKEN", "USERIO_LOGIN_CODE_TELEGRAM_BOT_TOKEN_FILE"),
        )


class LoginCodeService:
    """Issue and verify one-time codes, and bind identities to users."""

    def __init__(self, store: SQLiteUserIOStore, *, settings: LoginCodeSettings,
                 mailer: Callable[..., None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._settings = settings
        self._mailer = mailer or send_login_code_email
        self._clock = clock

    @property
    def settings(self) -> LoginCodeSettings:
        return self._settings

    def request_email_code(self, destination: str, *, request_ip: str = "") -> bool:
        """Send a login code to an address.

        The answer never depends on whether the address is registered, so the
        caller cannot be used to probe who has an account here.
        """

        if not self._settings.email_enabled:
            return False
        address = normalize_email(destination)
        if not address:
            return False
        now = self._clock()
        recent = self._store.login_challenge_count(
            channel=CHANNEL_EMAIL, destination=address,
            since=now - self._settings.destination_window_seconds)
        if recent >= self._settings.requests_per_destination:
            LOG.info("login code request suppressed by destination rate limit")
            return False
        from_ip = self._store.login_challenge_count_from_ip(
            request_ip=request_ip, since=now - self._settings.ip_window_seconds)
        if from_ip >= self._settings.requests_per_ip:
            LOG.info("login code request suppressed by client rate limit")
            return False
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        salt = secrets.token_bytes(16)
        challenge_id = self._store.create_login_challenge(
            channel=CHANNEL_EMAIL, destination=address, code_hash=hash_code(code, salt), salt=salt,
            ttl_seconds=self._settings.code_ttl_seconds, request_ip=request_ip)
        try:
            self._mailer(
                host=self._settings.smtp_host, port=self._settings.smtp_port,
                user=self._settings.smtp_user, password=self._settings.smtp_password,
                sender=self._settings.from_address, recipient=address, code=code,
                ttl_seconds=self._settings.code_ttl_seconds)
        except (OSError, smtplib.SMTPException) as error:
            LOG.warning("login code delivery failed: %s", type(error).__name__)
            self._store.consume_login_challenge(challenge_id)
            return False
        return True

    def verify_email_code(self, destination: str, code: str) -> UserPrincipal | None:
        if not self._settings.enabled:
            return None
        address = normalize_email(destination)
        if not address or not _CODE.fullmatch(str(code or "")):
            return None
        candidates = self._store.login_challenge_candidates(
            channel=CHANNEL_EMAIL, destination=address, now=self._clock())
        for candidate in candidates:
            if int(candidate["attempts"]) >= self._settings.max_attempts:
                continue
            if not verify_code(str(code), bytes(candidate["salt"]), bytes(candidate["code_hash"])):
                continue
            if not self._store.consume_login_challenge(str(candidate["id"])):
                continue
            return self._resolve(channel=CHANNEL_EMAIL, identifier=address,
                                 display_name=address.partition("@")[0])
        self._store.bump_login_challenge_attempts([str(item["id"]) for item in candidates])
        return None

    def verify_telegram_login(self, payload: Mapping[str, str]) -> UserPrincipal | None:
        if not self._settings.telegram_enabled:
            return None
        identity = verify_telegram_widget(
            payload, self._settings.telegram_bot_token,
            max_age=self._settings.telegram_auth_max_age, now=self._clock())
        if identity is None:
            return None
        return self._resolve(channel=CHANNEL_TELEGRAM, identifier=identity["id"],
                             display_name=identity.get("first_name") or identity.get("username") or "")

    def _resolve(self, *, channel: str, identifier: str, display_name: str) -> UserPrincipal | None:
        """Return the user behind a verified identity, registering it once."""

        principal = self._store.login_identity_user(channel=channel, identifier=identifier)
        if principal is not None:
            return principal
        if not self._settings.registration_enabled:
            LOG.info("login identity is not registered and registration is closed")
            return None
        principal, _token = self._store.create_login_user(
            channel=channel, identifier=identifier, display_name=display_name)
        return principal
