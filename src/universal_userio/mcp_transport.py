"""Newline-delimited JSON-RPC transport for the UserIO MCP surface."""

from __future__ import annotations

import json
from typing import Any, TextIO

from .mcp_surface import UserIOMcpSurface


class StdioJsonRpcTransport:
    def __init__(self, surface: UserIOMcpSurface, input_stream: TextIO, output_stream: TextIO) -> None:
        self._surface, self._input, self._output = surface, input_stream, output_stream

    def serve(self) -> None:
        for line in self._input:
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0": raise ValueError
                request_id, method = request.get("id"), request.get("method")
                if request_id is None: continue
                if method == "initialize": result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "universal-userio", "version": "0.1.0"}, "capabilities": {"tools": {}}}
                elif method in {"tools/list", "tools/call"}: result = self._surface.dispatch(method, request.get("params", {}))
                else: raise LookupError
                self._write({"jsonrpc": "2.0", "id": request_id, "result": result})
            except LookupError:
                self._write({"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None, "error": {"code": -32601, "message": "Method not found"}})
            except Exception:
                self._write({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})

    def _write(self, payload: dict[str, Any]) -> None:
        json.dump(payload, self._output, ensure_ascii=False, separators=(",", ":")); self._output.write("\n"); self._output.flush()
