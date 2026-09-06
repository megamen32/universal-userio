"""Newline-delimited JSON-RPC transport for the UserIO MCP surface."""

from __future__ import annotations

import json
from typing import Any, TextIO
from collections import defaultdict, deque
from threading import Condition

from .contracts import UserPrincipal
from .mcp_surface import UserIOMcpSurface



class ResourceSubscriptionHub:
    def __init__(self) -> None:
        self._condition = Condition()
        self._subscriptions: dict[str, set[str]] = defaultdict(set)
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(deque)

    def subscribe(self, user_id: str, uri: str) -> None:
        with self._condition:
            self._subscriptions[user_id].add(uri)

    def unsubscribe(self, user_id: str, uri: str) -> None:
        with self._condition:
            self._subscriptions[user_id].discard(uri)

    def publish(self, user_id: str, uri: str) -> None:
        with self._condition:
            if uri not in self._subscriptions.get(user_id, set()):
                return
            self._events[user_id].append({
                "jsonrpc": "2.0",
                "method": "notifications/resources/updated",
                "params": {"uri": uri},
            })
            self._condition.notify_all()

    def wait(self, user_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        with self._condition:
            if not self._events[user_id]:
                self._condition.wait(timeout)
            return self._events[user_id].popleft() if self._events[user_id] else None

def json_rpc_response(
    surface: UserIOMcpSurface, request: Any, *, principal: UserPrincipal | None = None, subscription_hub: ResourceSubscriptionHub | None = None
) -> dict[str, Any] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    request_id, method = request.get("id"), request.get("method")
    if request_id is None:
        return None
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, -32602, "Invalid params")
    try:
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            protocol = "2026-07-28" if requested == "2026-07-28" else "2024-11-05"
            result = {
                "protocolVersion": protocol,
                "serverInfo": {"name": "universal-userio", "version": "0.2.0"},
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": True, "listChanged": False},
                },
            }
        elif method in {"tools/list", "tools/call"}:
            result = surface.dispatch(method, params, principal=principal)
            if method == "tools/call":
                result = {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    }],
                    "structuredContent": result,
                    "isError": result.get("ok") is False,
                }
        elif method == "resources/list":
            result = surface.resource_manifest()
        elif method == "resources/templates/list":
            result = surface.resource_template_manifest()
        elif method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _error(request_id, -32602, "uri is required")
            result = surface.read_resource(uri, principal=principal)
        elif method in {"resources/subscribe", "resources/unsubscribe"}:
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _error(request_id, -32602, "uri is required")
            if uri != "userio://inbox/unread":
                return _error(request_id, -32602, "resource is not subscribable")
            if principal is None or subscription_hub is None:
                return _error(request_id, -32603, "subscriptions unavailable")
            if method == "resources/subscribe":
                subscription_hub.subscribe(principal.user_id, uri)
            else:
                subscription_hub.unsubscribe(principal.user_id, uri)
            result = {}
        elif method == "ping":
            result = {}
        else:
            return _error(request_id, -32601, "Method not found")
    except KeyError as error:
        return _error(request_id, -32002, str(error).strip("'") or "resource not found")
    except (TypeError, ValueError) as error:
        return _error(request_id, -32602, str(error) or "Invalid params")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def sse_message(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: message\ndata: {data}\n\n".encode()


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class StdioJsonRpcTransport:
    def __init__(self, surface: UserIOMcpSurface, input_stream: TextIO, output_stream: TextIO) -> None:
        self._surface, self._input, self._output = surface, input_stream, output_stream

    def serve(self) -> None:
        for line in self._input:
            try:
                request = json.loads(line)
                response = json_rpc_response(self._surface, request)
                if response is not None:
                    self._write(response)
            except Exception:
                self._write(_error(None, -32600, "Invalid Request"))

    def _write(self, payload: dict[str, Any]) -> None:
        json.dump(payload, self._output, ensure_ascii=False, separators=(",", ":")); self._output.write("\n"); self._output.flush()
