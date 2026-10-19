"""Newline-delimited JSON-RPC transport for the UserIO MCP surface."""

from __future__ import annotations

import json
from typing import Any, TextIO
from collections import defaultdict, deque
from threading import Condition

from .contracts import UserPrincipal
from .mcp_surface import UserIOMcpSurface

MODERN_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_MODERN_PROTOCOL_VERSIONS = [MODERN_PROTOCOL_VERSION]
_SERVER_INFO = {"name": "universal-userio", "version": "0.2.0"}


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
    request_meta = params.get("_meta")
    modern = isinstance(request_meta, dict) and "io.modelcontextprotocol/protocolVersion" in request_meta
    requested_protocol = request_meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(request_meta, dict) else None
    if modern and not isinstance(request_meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
        return _error(request_id, -32600, "Modern MCP request requires clientCapabilities metadata")
    if modern and requested_protocol not in SUPPORTED_MODERN_PROTOCOL_VERSIONS:
        return _error(request_id, -32022, "Unsupported protocol version", {
            "supported": SUPPORTED_MODERN_PROTOCOL_VERSIONS,
            "requested": requested_protocol,
        })
    if modern and method == "initialize":
        return _error(request_id, -32601, "Method not found")
    if method == "server/discover" and not modern:
        return _error(request_id, -32600, "server/discover requires modern MCP metadata")
    try:
        if method == "server/discover":
            can_read = principal is None or surface._store.capability_enabled("read", user_id=principal.user_id)
            result = {
                "resultType": "complete",
                "supportedVersions": SUPPORTED_MODERN_PROTOCOL_VERSIONS,
                "capabilities": {
                    **({"tools": {}} if can_read else {}),
                    **({"resources": {}} if can_read else {}),
                },
                "_meta": {"io.modelcontextprotocol/serverInfo": _SERVER_INFO},
                "instructions": "UserIO reads user-owned conversations and exposes bounded message media to MCP tools.",
                "ttlMs": 300000,
                "cacheScope": "private" if principal is not None else "public",
            }
        elif method == "initialize":
            # This transport implements the legacy initialize/initialized era.
            # The 2026-07-28 revision removes this handshake and uses
            # server/discover plus per-request _meta, so never claim it here.
            protocol = "2024-11-05"
            can_read = principal is None or surface._store.capability_enabled("read", user_id=principal.user_id)
            can_subscribe = principal is None or (can_read and surface._store.capability_enabled("subscribe", user_id=principal.user_id))
            result = {
                "protocolVersion": protocol,
                "serverInfo": _SERVER_INFO,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": can_subscribe, "listChanged": False},
                },
            }
        elif method in {"tools/list", "tools/call"}:
            result = surface.dispatch(method, params, principal=principal)
            if method == "tools/call":
                result = dict(result)
                media_blocks = result.pop("_mcp_content_blocks", [])
                content = [{
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                }]
                if isinstance(media_blocks, list):
                    content.extend(block for block in media_blocks if isinstance(block, dict))
                result = {
                    "content": content,
                    "structuredContent": result,
                    "isError": result.get("ok") is False,
                }
        elif method == "resources/list":
            result = surface.resource_manifest(principal=principal)
        elif method == "resources/templates/list":
            result = surface.resource_template_manifest(principal=principal)
        elif method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _error(request_id, -32602, "uri is required")
            result = surface.read_resource(uri, principal=principal)
        elif method in {"resources/subscribe", "resources/unsubscribe"}:
            if modern:
                return _error(request_id, -32601, "Method not found")
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _error(request_id, -32602, "uri is required")
            if uri != "userio://inbox/unread":
                return _error(request_id, -32602, "resource is not subscribable")
            if principal is None or subscription_hub is None:
                return _error(request_id, -32603, "subscriptions unavailable")
            if not surface._store.capability_enabled("read", user_id=principal.user_id):
                return _error(request_id, -32003, "read capability disabled")
            if not surface._store.capability_enabled("subscribe", user_id=principal.user_id):
                return _error(request_id, -32003, "subscribe capability disabled")
            if method == "resources/subscribe":
                subscription_hub.subscribe(principal.user_id, uri)
            else:
                subscription_hub.unsubscribe(principal.user_id, uri)
            result = {}
        elif method == "ping" and not modern:
            result = {}
        else:
            return _error(request_id, -32601, "Method not found")
    except PermissionError as error:
        return _error(request_id, -32003, str(error))
    except KeyError as error:
        return _error(request_id, -32002, str(error).strip("'") or "resource not found")
    except (TypeError, ValueError) as error:
        return _error(request_id, -32602, str(error) or "Invalid params")
    if modern:
        result = dict(result)
        result.setdefault("resultType", "complete")
        if method != "server/discover":
            result.setdefault("_meta", {"io.modelcontextprotocol/serverInfo": _SERVER_INFO})
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def sse_message(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: message\ndata: {data}\n\n".encode()


def _error(request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


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
