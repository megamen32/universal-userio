"""The Secretary reads its own assigned chats over the scoped UserIO MCP.

The scoped read-only token never enters env, logs, or plain JSON: the
secretary voice config stores a file reference, the token is read from that
0600 file at call time, and the read goes over the UserIO Streamable HTTP MCP
with per-token grants enforced server-side. Workspace chat rules govern
auto-processing and stay untouched: this reader works on inherit/ignore chats
alike, and the grants still refuse anything outside the token scope.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_USERIO_MCP_URL = "https://msg.bezrabotnyi.com/mcp"
DEFAULT_CONFIG_FILE = "/etc/agentcall-secretary-voice.json"
_TOKEN_LIMIT = 8192


class UserIOChatsError(RuntimeError):
    """A scoped chat read failed clearly instead of returning silence."""


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def userio_mcp_token_file(config_file: str | None = None) -> Path:
    """Resolve the 0600 token-file reference from the secretary voice config.

    The config stores the file path, never the token value. The referenced
    file must be owned by the current service user and mode 0600 — the same
    private-credential contract as the AgentCall runtime context.
    """
    path = Path(config_file or os.environ.get(
        "EXMANAGER_VOICE_CONFIG_FILE", DEFAULT_CONFIG_FILE))
    if not path.is_file():
        raise UserIOChatsError(
            f"Secretary voice config is missing: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    reference = _bounded_text(config.get("USERIO_MCP_TOKEN_FILE"), 512)
    if not reference:
        raise UserIOChatsError(
            "USERIO_MCP_TOKEN_FILE is not set in the secretary voice config")
    resolved = Path(reference)
    if not resolved.is_file():
        raise UserIOChatsError(
            f"UserIO MCP token file is missing: {resolved}")
    metadata = resolved.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PermissionError(
            "UserIO MCP token file must be owned by the service user and mode 0600")
    return resolved


def userio_mcp_token(config_file: str | None = None) -> str:
    path = userio_mcp_token_file(config_file)
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise UserIOChatsError("UserIO MCP token file is empty")
    return token


class UserIOChatsStore:
    """Read-only search and read over the scoped UserIO MCP endpoint.

    The scoped token grants read_only on all chats of exactly the assigned
    accounts; the server refuses anything outside that scope. This reader
    adds no send capability and never marks chats as read.
    """

    def __init__(self, mcp_url=None, token_file=None, *,
                 timeout=20.0, runner=None):
        self.mcp_url = str(mcp_url or os.environ.get(
            "USERIO_MCP_URL", DEFAULT_USERIO_MCP_URL)).strip().rstrip('/')
        self.token_file = str(token_file or "").strip()
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self.runner = runner or _direct_post
        self._session_id = None

    def _headers(self):
        if self.token_file:
            path = Path(self.token_file)
            if not path.is_file():
                raise UserIOChatsError(
                    f"UserIO MCP token file is missing: {path}")
            metadata = path.stat()
            if metadata.st_mode & 0o077:
                raise PermissionError(
                    "UserIO MCP token file must be mode 0600")
            token = path.read_text(encoding="utf-8").strip()
        else:
            token = userio_mcp_token()
        if not token:
            raise UserIOChatsError("UserIO MCP token file is empty")
        return {"Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"}

    def _post(self, body: dict) -> tuple[int, str, str]:
        headers = self._headers()
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(
            self.mcp_url, data=json.dumps(body, ensure_ascii=False).encode(),
            headers=headers, method="POST")
        try:
            with self.runner(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                payload = response.read().decode("utf-8", "replace")
                session = response.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as error:
            detail = error.read(512).decode("utf-8", "replace")
            raise UserIOChatsError(
                f"UserIO MCP returned HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UserIOChatsError(
                f"UserIO MCP is unreachable ({type(error).__name__})") from None
        if session:
            self._session_id = session
        return status, payload, session

    @staticmethod
    def _payload(raw: str) -> dict:
        if "data:" in raw:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return json.loads(raw)

    def _initialize(self) -> None:
        status, payload, _ = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "secretary-voice", "version": "1"}}})
        if status != 200:
            raise UserIOChatsError(
                f"UserIO MCP initialize returned HTTP {status}")

    def _call(self, name: str, arguments: dict) -> dict:
        self._initialize() if self._session_id is None else None
        status, raw, _ = self._post({"jsonrpc": "2.0", "id": 2,
                                     "method": "tools/call",
                                     "params": {"name": name, "arguments": arguments}})
        if status != 200:
            raise UserIOChatsError(
                f"UserIO MCP tools/call returned HTTP {status}")
        payload = self._payload(raw)
        result = payload.get("result") or {}
        contents = [item.get("text", "") for item in result.get("content", [])
                    if isinstance(item, dict)]
        if result.get("isError"):
            detail = contents[0] if contents else "unknown tool error"
            raise UserIOChatsError(f"UserIO MCP refused: {detail}")
        if not contents:
            raise UserIOChatsError("UserIO MCP returned an empty tool result")
        try:
            return json.loads(contents[0])
        except ValueError:
            raise UserIOChatsError(
                "UserIO MCP returned a non-object tool result") from None

    def search(self, query: str, *, account_ref: str | None = None,
               limit: int = 10) -> list[dict[str, Any]]:
        """Search the granted chats; inherited/ignored chats are included."""
        arguments: dict[str, Any] = {"query": _bounded_text(query, 256),
                                     "limit": max(1, min(int(limit), 50))}
        if account_ref:
            arguments["account_ref"] = account_ref
        found = self._call("userio.channels.search", arguments)
        return found.get("results") or []

    def read(self, chat_id: str, *, history_limit: int | None = None) -> dict[str, Any]:
        """Read one granted chat with recent messages (bounded)."""
        arguments: dict[str, Any] = {"channel": "telegram",
                                     "chat_id": _bounded_text(chat_id, 128)}
        if history_limit is not None:
            arguments["history_limit"] = max(1, min(int(history_limit), 50))
        return self._call("userio.channels.read", arguments)


def _direct_post(request, timeout=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout or 20) as response:
        return (response.status, response.read().decode("utf-8", "replace"),
                response.headers.get("Mcp-Session-Id"))
