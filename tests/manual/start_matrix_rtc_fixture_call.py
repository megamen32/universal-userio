#!/usr/bin/env python3
"""Start or reconcile one exact fixture UserIO MatrixRTC call.

Run inside ``userio-backend`` after the reviewed per-user call route is deployed.
The script mints a narrowly named, ten-minute API token in memory, calls the
real HTTP endpoint once, reconciles by its fixed request ID, and revokes the
token in ``finally``. It never accepts a target, account, room or token on argv.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid


DB = "/var/lib/universal-userio/userio.sqlite3"
API = "https://msg.bezrabotnyi.com"
ROOM = "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"
FIXTURES = {
    "a": {
        "username": "lhc_voice_user_a_20261021",
        "matrix_id": "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com",
        "target": "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com",
        "request_id": "lhc_matrixrtc_20261021_a2b",
    },
    "b": {
        "username": "lhc_voice_user_b_20261021",
        "matrix_id": "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com",
        "target": "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com",
        "request_id": "lhc_matrixrtc_20261021_b2a",
    },
}
EXPECTED_MODEL = "omniroute/minimax-combo-m3.1"
TOKEN_TTL_SECONDS = 600
CALL_TIMEOUT_SECONDS = 360


class _ExistingFixtureStore:
    """Narrow adapter for PersonalTokens without running startup migrations."""

    def __init__(self):
        self._connection = sqlite3.connect(f"file:{DB}?mode=rw", uri=True, timeout=5)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=5000")
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(api_tokens)")}
        if not {"name", "scope", "expires_at"}.issubset(columns):
            self._connection.close()
            raise RuntimeError("personal_token_schema_not_ready")
        self._lock = threading.RLock()

    @staticmethod
    def _token_hash(token):
        from universal_userio.store import SQLiteUserIOStore
        return SQLiteUserIOStore._token_hash(token)

    def close(self):
        self._connection.close()


def _fixture_state(store, alias):
    fixture = FIXTURES[alias]
    row = store._connection.execute(
        "SELECT u.id,u.role,a.id AS account_id,a.enabled,a.credential_ref,"
        "s.endpoint,s.model,s.token FROM users u "
        "JOIN provider_accounts a ON a.user_id=u.id AND a.provider='matrix' "
        "JOIN user_ai_settings s ON s.user_id=u.id "
        "WHERE u.username=? AND a.enabled=1",
        (fixture["username"],),
    ).fetchone()
    if not row or row["role"] != "user" or row["model"] != EXPECTED_MODEL:
        raise PermissionError("fixture_principal_or_model_mismatch")
    if (not row["token"] or urlsplit(str(row["endpoint"])).scheme != "https"
            or urlsplit(str(row["endpoint"])).hostname != "omniroute.bezrabotnyi.com"):
        raise PermissionError("fixture_ai_settings_missing")
    from universal_userio.account_connections import ConnectorSecrets
    account = ConnectorSecrets().load(
        str(row["credential_ref"]), user_id=str(row["id"]), provider="matrix",
    )
    if account.get("matrix_user_id") != fixture["matrix_id"]:
        raise PermissionError("fixture_matrix_identity_mismatch")
    return fixture, str(row["id"]), str(row["account_id"])


def _payload(fixture, account_ref):
    return {
        "account_ref": account_ref,
        "room_id": ROOM,
        "target": fixture["target"],
        "purpose": (
            "Закрытый тестовый разговор двух выданных владельцем Matrix-ботов. "
            "Отыгрывай владельца: сначала спроси о текущих задачах, затем попроси "
            "рассказать о свежих инцидентах, после ответа попроси вернуться к "
            "режиму Secretary. Не выдумывай личные сведения и подтверждай только "
            "то, что действительно услышал."
        ),
        "greeting": "Привет. Расскажи, пожалуйста, какие у меня сейчас задачи?",
        "request_id": fixture["request_id"],
        "max_seconds": 240,
        "confirm": True,
    }


def _call_id(user_id, request_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, user_id + "\0" + request_id))


def _request(method, path, token, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        API + path, data=body, method=method,
        headers={"Authorization": "Bearer " + token,
                 **({"Content-Type": "application/json"} if body is not None else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read(1024 * 1024 + 1))
    except urllib.error.HTTPError as error:
        # Keep only the status code; error bodies can contain provider details.
        raise RuntimeError("userio_http_" + str(error.code)) from None


def run(alias):
    from universal_userio.personal_tokens import PersonalTokens

    store = _ExistingFixtureStore()
    token_id = token = user_id = call_id = None
    try:
        fixture, user_id, account_ref = _fixture_state(store, alias)
        call_id = _call_id(user_id, fixture["request_id"])
        tokens = PersonalTokens(store)
        issued = tokens.create(
            user_id, "temporary LHC MatrixRTC fixture call", scope="full_access",
        )
        token_id, token = issued["id"], issued["token"]
        with store._lock, store._connection:
            store._connection.execute(
                "UPDATE api_tokens SET expires_at=? WHERE id=? AND user_id=? "
                "AND revoked_at IS NULL",
                (time.time() + TOKEN_TTL_SECONDS, token_id, user_id),
            )

        # An existing accepted attempt is reconciled; never POST it a second time.
        try:
            status, result = _request("GET", "/v1/calls/matrix/" + call_id, token)
            record = result["call"]
        except RuntimeError as error:
            if str(error) != "userio_http_404":
                raise
            try:
                status, result = _request("POST", "/v1/calls/matrix", token, _payload(fixture, account_ref))
            except Exception:
                # The POST may have been accepted before transport failed.
                # Reconcile the same deterministic id; never replay the POST.
                try:
                    _, result = _request("GET", "/v1/calls/matrix/" + call_id, token)
                    record = result["call"]
                except Exception:
                    raise RuntimeError("call_acceptance_unknown_reconcile_required") from None
            else:
                record = result["call"]
            if str(record.get("call_id")) != call_id:
                raise RuntimeError("call_receipt_id_mismatch")

        deadline = time.monotonic() + CALL_TIMEOUT_SECONDS
        while record.get("status") in {"accepted", "ringing", "active"} and time.monotonic() < deadline:
            time.sleep(2)
            _, result = _request("GET", "/v1/calls/matrix/" + call_id, token)
            record = result["call"]
        if record.get("status") in {"accepted", "ringing", "active"}:
            try:
                _, result = _request("POST", "/v1/calls/matrix/" + call_id + "/stop", token, {})
                record = result["call"]
            except Exception:
                pass
        print(json.dumps({
            "fixture": alias,
            "call_id": call_id,
            "status": record.get("status"),
            "answered": bool(record.get("answered")),
            "acknowledged": bool(record.get("acknowledged")),
            "received_audio_bytes": int(record.get("received_audio_bytes") or 0),
            "warning": record.get("warning"),
        }), flush=True)
        return 0 if record.get("status") == "completed" and record.get("answered") else 2
    except Exception as error:
        print(json.dumps({"event": "error", "reason": type(error).__name__,
                          "detail": str(error)[:80] if str(error).startswith("userio_http_") else None}), flush=True)
        return 1
    finally:
        if token_id and user_id:
            try:
                PersonalTokens(store).revoke(user_id, token_id)
            except Exception:
                pass
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", choices=tuple(FIXTURES))
    return run(parser.parse_args().fixture)


if __name__ == "__main__":
    raise SystemExit(main())
