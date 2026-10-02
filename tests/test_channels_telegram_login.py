"""Tests for QR session provisioning (overpod-style interactive + TGC mint)."""

from __future__ import annotations

import asyncio

import pytest
from telethon.crypto import AuthKey
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.auth import AcceptLoginTokenRequest

import universal_userio.channels.telegram_login as telegram_login
from universal_userio.channels.telegram_login import (
    QrLoginResult,
    create_independent_session_via_qr,
    login_via_qr,
    session_to_string,
)


class FakeSession:
    dc_id = 2
    server_address = "149.154.167.51"
    port = 443

    def __init__(self) -> None:
        self.auth_key = AuthKey(b"\x01" * 256)
        self.saved = 0

    def set_dc(self, dc_id, server_address, port) -> None:
        self.dc_id, self.server_address, self.port = dc_id, server_address, port

    def save(self) -> None:
        self.saved += 1


class FakeQr:
    url = "tg://login?token=abc123"
    token = b"token-bytes"

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode

    async def wait(self):
        if self.mode == "ok":
            return True
        if self.mode == "twofa":
            raise SessionPasswordNeededError(request=None)
        raise asyncio.TimeoutError()

    async def recreate(self) -> "FakeQr":
        return FakeQr(self.mode)


class FakeClient:
    def __init__(self, qr_mode: str = "ok") -> None:
        self.session = FakeSession()
        self._qr_mode = qr_mode
        self.connected = False
        self.authorized = True
        self.sign_in_calls: list[str | None] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def qr_login(self):
        return FakeQr(self._qr_mode)

    async def sign_in(self, *, password: str | None = None) -> None:
        self.sign_in_calls.append(password)


class FakeAuthorizedClient:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.calls: list[object] = []

    async def __call__(self, request):
        self.calls.append(request)
        return object()


def test_session_to_string_roundtrip_shape() -> None:
    value = session_to_string(FakeSession())
    assert isinstance(value, str) and len(value) > 50


def test_login_via_qr_ok_renders_url_and_builds_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_login, "QR_POLL_SECONDS", 0.05)
    rendered: list[str] = []
    client = FakeClient("ok")

    result = asyncio.run(
        login_via_qr(
            api_id=10,
            api_hash="hash",
            session_path=tmp_path / "s.session",
            renderer=rendered.append,
            client_factory=lambda value: client,
        )
    )

    assert result.ok is True
    assert rendered == ["tg://login?token=abc123"]
    assert result.new_session_string and len(result.new_session_string) > 50
    assert result.session_path == tmp_path / "s.session"


def test_login_via_qr_answers_twofa_password(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_login, "QR_POLL_SECONDS", 0.05)
    client = FakeClient("twofa")

    result = asyncio.run(
        login_via_qr(
            api_id=10,
            api_hash="hash",
            password="srp-secret",
            session_path=tmp_path / "s.session",
            renderer=lambda url: None,
            client_factory=lambda value: client,
        )
    )

    assert result.ok is True
    assert client.sign_in_calls == ["srp-secret"]


def test_login_via_qr_twofa_without_password_reports_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_login, "QR_POLL_SECONDS", 0.05)
    client = FakeClient("twofa")

    result = asyncio.run(
        login_via_qr(
            api_id=10,
            api_hash="hash",
            session_path=tmp_path / "s.session",
            renderer=lambda url: None,
            client_factory=lambda value: client,
        )
    )

    assert result.ok is False
    assert result.reason == "twofa_required"


def test_login_via_qr_timeout_reports_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_login, "QR_POLL_SECONDS", 0.05)
    client = FakeClient("timeout")

    result = asyncio.run(
        login_via_qr(
            api_id=10,
            api_hash="hash",
            timeout_seconds=1,
            session_path=tmp_path / "s.session",
            renderer=lambda url: None,
            client_factory=lambda value: client,
        )
    )

    assert result.ok is False
    assert result.reason == "qr_timeout"
    assert client.connected is False


def test_create_independent_session_accepts_token_via_authorized_client() -> None:
    authorized = FakeAuthorizedClient()
    client = FakeClient("ok")

    result = asyncio.run(
        create_independent_session_via_qr(
            authorized_client=authorized,
            client_factory=lambda value: client,
        )
    )

    assert result.ok is True
    assert result.new_session_string
    assert len(authorized.calls) == 1
    assert isinstance(authorized.calls[0], AcceptLoginTokenRequest)


def test_create_independent_session_timeout_reason() -> None:
    authorized = FakeAuthorizedClient()
    client = FakeClient("timeout")

    result = asyncio.run(
        create_independent_session_via_qr(
            authorized_client=authorized,
            accept_timeout_seconds=0.05,
            client_factory=lambda value: client,
        )
    )

    assert isinstance(result, QrLoginResult)
    assert result.ok is False
    assert result.reason == "qr_timeout"
