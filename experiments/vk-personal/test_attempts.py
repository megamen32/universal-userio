"""Login attempt ownership and anti-mixup tests before any UserIO wiring."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

SPEC = importlib.util.spec_from_file_location(
    "vk_attempts", Path(__file__).with_name("attempts.py"),
)
attempts = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
import sys
sys.modules["vk_attempts"] = attempts
SPEC.loader.exec_module(attempts)


def test_two_userio_users_have_distinct_attempts_and_oauth_state():
    clock = lambda: 1000.
    registry = attempts.VKLoginAttempts(clock=clock)
    a, url_a = registry.begin(owner_id="owner_A", expected_vk_id="46887791")
    b, url_b = registry.begin(owner_id="owner_B", expected_vk_id="46887791")
    assert a.attempt_id != b.attempt_id and a.state != b.state
    assert a.client_id == b.client_id == "51453752"
    assert parse_qs(urlsplit(url_a).query)["state"] == [a.state]
    assert parse_qs(urlsplit(url_b).query)["state"] == [b.state]
    assert parse_qs(urlsplit(url_a).query)["client_id"] == ["51453752"]


def test_attempt_bound_to_user_and_one_time_state():
    registry = attempts.VKLoginAttempts(clock=lambda: 1000.)
    attempt, _ = registry.begin(owner_id="owner_A", expected_vk_id="46887791")
    with pytest.raises(PermissionError):
        registry.consume(
            owner_id="owner_B", attempt_id=attempt.attempt_id,
            callback_state=attempt.state, actual_vk_id="46887791",
        )
    with pytest.raises(ValueError, match="state"):
        registry.consume(
            owner_id="owner_A", attempt_id=attempt.attempt_id,
            callback_state="wrong-state", actual_vk_id="46887791",
        )
    with pytest.raises(ValueError, match="state"):
        registry.consume(
            owner_id="owner_A", attempt_id=attempt.attempt_id,
            callback_state="", actual_vk_id="46887791",
        )
    with pytest.raises(ValueError, match="not the one requested"):
        registry.consume(
            owner_id="owner_A", attempt_id=attempt.attempt_id,
            callback_state=attempt.state, actual_vk_id="42",
        )
    assert registry.consume(
        owner_id="owner_A", attempt_id=attempt.attempt_id,
        callback_state=attempt.state, actual_vk_id="46887791",
    ).owner_id == "owner_A"
    with pytest.raises(ValueError, match="No such"):
        registry.consume(
            owner_id="owner_A", attempt_id=attempt.attempt_id,
            callback_state=attempt.state, actual_vk_id="46887791",
        )


def test_attempt_expiry_and_cancellation():
    now = [1_000.]
    registry = attempts.VKLoginAttempts(clock=lambda: now[0], ttl_seconds=300)
    attempt, _ = registry.begin(owner_id="owner_A")
    now[0] += 301
    with pytest.raises(ValueError, match="expired"):
        registry.consume(
            owner_id="owner_A", attempt_id=attempt.attempt_id,
            callback_state=attempt.state, actual_vk_id="42",
        )
    another, _ = registry.begin(owner_id="owner_A")
    with pytest.raises(PermissionError):
        registry.cancel(owner_id="owner_B", attempt_id=another.attempt_id)
    registry.cancel(owner_id="owner_A", attempt_id=another.attempt_id)
    with pytest.raises(ValueError, match="No such"):
        registry.consume(
            owner_id="owner_A", attempt_id=another.attempt_id,
            callback_state=another.state, actual_vk_id="42",
        )
