"""HTTP-facing composition for the transport-independent UserIO MCP surface."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .contracts import UserPrincipal
from .mcp_surface import UserIOMcpSurface
from .mcp_transport import ResourceSubscriptionHub, json_rpc_response, sse_message


class McpHttpEndpoint:
    """Own HTTP MCP request dispatch and per-user resource notifications."""

    def __init__(self, surface: UserIOMcpSurface) -> None:
        self._surface = surface
        self._subscriptions = ResourceSubscriptionHub()

    def post(
        self, request: Any, *, principal: UserPrincipal
    ) -> dict[str, Any] | None:
        return json_rpc_response(
            self._surface,
            request,
            principal=principal,
            subscription_hub=self._subscriptions,
        )

    def publish_inbox_update(self, user_id: str) -> None:
        self._subscriptions.publish(user_id, "userio://inbox/unread")

    def event_stream(
        self, principal: UserPrincipal, *, timeout: float = 30.0
    ) -> Iterator[bytes]:
        ready = {
            "jsonrpc": "2.0",
            "method": "userio/ready",
            "params": {"endpoint": "/mcp", "username": principal.username},
        }
        yield sse_message(ready)
        event = self._subscriptions.wait(principal.user_id, timeout=timeout)
        if event is not None:
            yield sse_message(event)

    @staticmethod
    def encode_sse(payload: dict[str, Any]) -> bytes:
        """Encode a JSON-RPC response for an HTTP SSE response."""

        return sse_message(payload)

    @staticmethod
    def unauthorized(base_url: str) -> tuple[dict[str, str], dict[str, str]]:
        metadata = base_url + "/.well-known/oauth-protected-resource"
        return (
            {"error": "unauthorized"},
            {"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
        )
