from __future__ import annotations

import urllib.error

import pytest

from universal_userio.adapters import _download_via_bridge
from universal_userio.channels.core import AdapterNotSupported


def test_ai_bridge_download_stops_before_unbounded_allocation() -> None:
    class Response:
        headers = {"Content-Type": "image/png"}
        requested: int | None = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int | None = None) -> bytes:
            self.requested = size
            return b"x" * int(size or 0)

    response = Response()

    with pytest.raises(AdapterNotSupported, match="exceeds the bounded AI image limit"):
        _download_via_bridge(
            channel="telegram",
            message={"sender": "42"},
            file_ref="1",
            bridge_url="http://bridge.invalid",
            token_env="MISSING_TEST_TOKEN",
            chat_field="chat",
            chat_id_field="chat_id",
            message_field="message_id",
            runner=lambda *_args, **_kwargs: response,
            max_bytes=1024,
        )

    assert response.requested == 1025


def test_ai_bridge_error_body_is_bounded() -> None:
    class ErrorBody:
        requested: int | None = None

        def read(self, size: int | None = None) -> bytes:
            self.requested = size
            return b"x" * int(size or 0)

        def close(self) -> None:
            pass

    body = ErrorBody()

    def runner(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://bridge.invalid", 502, "bad gateway", {}, body,
        )

    with pytest.raises(AdapterNotSupported, match="HTTP 502"):
        _download_via_bridge(
            channel="telegram",
            message={"sender": "42"},
            file_ref="1",
            bridge_url="http://bridge.invalid",
            token_env="MISSING_TEST_TOKEN",
            chat_field="chat",
            chat_id_field="chat_id",
            message_field="message_id",
            runner=runner,
            max_bytes=1024,
        )

    assert body.requested == 201
