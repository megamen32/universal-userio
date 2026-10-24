#!/usr/bin/env python3
"""QA-only side API: the product HTTP API for one Matrix call, without pollers.

Why this exists (canary mavis09, leaf L04): the measured path needs the
caller side on the same fixed bridge as the answering side, so the API that
places the call has to live in the same side container as the voice gateway
and must not be the live one.

Only the background pollers of universal_userio.runtime are left out (account
poller, Telegram delivery reconciliation, webhook delivery): they would touch
the owner's live channels.  Everything that decides the call is the product
code: http_api.handler, UserIOService, matrix_calls.MatrixCalls.
"""
from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

from universal_userio.http_api import handler
from universal_userio.runtime import build_service, load_runtime_identity


def main() -> int:
    environment = os.environ
    service = build_service(environment)
    identity = load_runtime_identity(
        environment.get("USERIO_RELEASE_FILE", "/opt/universal-userio/.userio-release.json")
    )
    server = ThreadingHTTPServer(
        (environment.get("USERIO_HOST", "127.0.0.1"), int(environment.get("USERIO_PORT", "18095"))),
        handler(
            service,
            token=environment.get("USERIO_API_TOKEN", ""),
            vkid_app_id=environment.get("USERIO_VKID_APP_ID", ""),
            trusted_proxy_token=environment.get("USERIO_TRUSTED_PROXY_TOKEN", ""),
            runtime_identity=identity,
        ),
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
