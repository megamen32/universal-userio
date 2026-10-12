"""Future UserIO VK login attempt state machine; not wired to production.

A provider callback must return the *same* state that began the login. The
existing manual-paste OAuth result may not include it; in that case the
callback-driven flow is NOT verified and must never be claimed CSRF-safe.
"""
from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time
from urllib.parse import urlencode

VK_MESSENGER_CLIENT_ID = "51453752"
VK_LOGIN_URI = "https://oauth.vk.ru/authorize"
VK_REDIRECT_URI = "https://oauth.vk.ru/blank.html"


@dataclass(frozen=True)
class LoginAttempt:
    owner_id: str
    attempt_id: str
    state: str
    expected_vk_id: str
    created_at: float
    expires_at: float
    client_id: str


class VKLoginAttempts:
    def __init__(self, *, clock=time.monotonic, ttl_seconds: int = 600):
        if not 60 <= ttl_seconds <= 900:
            raise ValueError("VK attempt TTL must be between 60 and 900 seconds")
        self._clock = clock
        self._ttl = ttl_seconds
        self._records: dict[str, LoginAttempt] = {}
        self._lock = threading.RLock()

    def begin(self, *, owner_id: str, expected_vk_id: str = "") -> tuple[LoginAttempt, str]:
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError("UserIO owner is required")
        if expected_vk_id and (not expected_vk_id.isdecimal() or len(expected_vk_id) > 20):
            raise ValueError("Expected VK ID must be numeric")
        created = self._clock()
        attempt = LoginAttempt(
            owner_id=owner_id,
            attempt_id=secrets.token_urlsafe(32),
            state=secrets.token_urlsafe(32),
            expected_vk_id=expected_vk_id,
            created_at=created,
            expires_at=created + self._ttl,
            client_id=VK_MESSENGER_CLIENT_ID,
        )
        with self._lock:
            self._records = {
                key: value for key, value in self._records.items()
                if value.expires_at > created
            }
            self._records[attempt.attempt_id] = attempt
        parameters = {
            "client_id": attempt.client_id,
            "display": "page",
            "scope": "messages,offline",
            "response_type": "token",
            "redirect_uri": VK_REDIRECT_URI,
            "state": attempt.state,
        }
        return attempt, VK_LOGIN_URI + "?" + urlencode(parameters)

    def consume(
        self, *, owner_id: str, attempt_id: str,
        callback_state: str, actual_vk_id: str,
    ) -> LoginAttempt:
        # A manual-paste payload without a provider-echoed state must not
        # masquerade as a verified automatic callback.
        with self._lock:
            attempt = self._records.get(attempt_id)
            if attempt is None:
                raise ValueError("No such VK authorization attempt")
            if attempt.expires_at <= self._clock():
                self._records.pop(attempt_id, None)
                raise ValueError("VK authorization attempt expired")
            if not secrets.compare_digest(attempt.owner_id, owner_id):
                raise PermissionError("Attempt belongs to a different UserIO user")
            if not callback_state or not secrets.compare_digest(attempt.state, callback_state):
                raise ValueError("VK callback did not match the original state")
            if (attempt.expected_vk_id and
                    not secrets.compare_digest(attempt.expected_vk_id, actual_vk_id)):
                raise ValueError("VK account is not the one requested")
            self._records.pop(attempt_id, None)
        return attempt

    def cancel(self, *, owner_id: str, attempt_id: str) -> None:
        with self._lock:
            attempt = self._records.get(attempt_id)
            if attempt is None:
                return
            if not secrets.compare_digest(attempt.owner_id, owner_id):
                raise PermissionError("Attempt belongs to a different UserIO user")
            self._records.pop(attempt_id, None)
