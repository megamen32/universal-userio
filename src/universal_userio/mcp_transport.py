"""Newline-delimited JSON-RPC transport for the UserIO MCP surface."""

from __future__ import annotations

import json
from typing import Any, TextIO

from .contracts import UserPrincipal
from .mcp_surface import UserIOMcpSurface


def json_rpc_response(
    surface: UserIOMcpSurface, request: Any, *, principal: UserPrincipal | None = None
) -> dict[str, Any] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    request_id, method = request.get("id"), request.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "universal-userio", "version": "0.2.0"},
            "capabilities": {"tools": {}},
        }
    elif method in {"tools/list", "tools/call"}:
        result = surface.dispatch(method, request.get("params", {}), principal=principal)
        if method == "tools/call":
            result = {
                "content": [{
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                }],
                "structuredContent": result,
                "isError": result.get("ok") is False,
            }
    else:
        return _error(request_id, -32601, "Method not found")
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
