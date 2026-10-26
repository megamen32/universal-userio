"""The Secretary reads its own assigned chats through the scoped UserIO MCP.

The scoped read-only token never enters env, logs, or plain JSON: the secretary
voice config stores a file reference, the token is read from that 0600 file at
call time, and the read goes over the UserIO Streamable HTTP MCP with per-token
grants enforced server-side. Inherited/ignored chats are readable alike, and
the grants still refuse anything outside the token scope.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from universal_userio.voice.userio_chats_context import (
    UserIOChatsError,
    UserIOChatsStore,
    userio_mcp_token,
    userio_mcp_token_file,
)


def sse(payload: dict, session: str | None = None) -> tuple[int, str, str]:
    raw = f"event: message\ndata: {json.dumps(payload)}\n\n"
    return 200, raw, session or "sess-1"


class TokenFileContractTests(unittest.TestCase):
    """The config stores a file reference; the referenced file is 0600."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config = self.root / "secretary-voice.json"

    def tearDown(self):
        self.directory.cleanup()

    def _write(self, token="uio_scoped_value") -> Path:
        path = self.root / "secretary-readonly-runtime.token"
        path.write_text(token + "\n")
        path.chmod(0o600)
        os.chown(path, os.geteuid(), os.geteuid())
        return path

    def test_the_token_file_reference_is_resolved_and_read(self):
        self.config.write_text(json.dumps({"USERIO_MCP_TOKEN_FILE": str(self._write())}))
        resolved = userio_mcp_token_file(str(self.config))
        self.assertEqual("uio_scoped_value", userio_mcp_token(str(self.config)))
        self.assertTrue(resolved.is_file())

    def test_a_world_readable_token_file_is_refused(self):
        path = self._write()
        path.chmod(0o644)
        self.config.write_text(json.dumps({"USERIO_MCP_TOKEN_FILE": str(path)}))
        with self.assertRaisesRegex(PermissionError, "0600"):
            userio_mcp_token(str(self.config))

    def test_a_missing_reference_is_a_clear_failure(self):
        self.config.write_text(json.dumps({"other": "key"}))
        with self.assertRaisesRegex(UserIOChatsError, "USERIO_MCP_TOKEN_FILE"):
            userio_mcp_token(str(self.config))

    def test_the_token_value_never_lands_in_the_config(self):
        self.config.write_text(json.dumps({"USERIO_MCP_TOKEN_FILE": str(self._write())}))
        userio_mcp_token(str(self.config))
        self.assertNotIn("uio_scoped_value", self.config.read_text())


class Response:
    def __init__(self, payload: dict, status: int = 200, session: str = "sess-1"):
        self.raw = f"event: message\ndata: {json.dumps(payload)}\n\n"
        self.status = status
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.raw.encode()

    @property
    def headers(self):
        return {"Mcp-Session-Id": self.session}


class ScopedChatsReadTests(unittest.TestCase):
    """The scoped read: granted inherit-groups readable, outside scope refused."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.token_file = self.root / "secretary-readonly-runtime.token"
        self.token_file.write_text("uio_scoped_value\n")
        self.token_file.chmod(0o600)
        self.store = UserIOChatsStore(
            mcp_url="https://msg.example.org/mcp", token_file=str(self.token_file), timeout=5)

    def tearDown(self):
        self.directory.cleanup()

    def test_the_scoped_history_read_returns_messages(self) -> None:
        content = {"ok": True, "schema": "userio.messenger.v1", "messages": [
            {"direction": "incoming", "sender": "qa deusexcode", "text": "рш"}]}
        script = [
            Response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26",
                      "serverInfo": {"name": "universal-userio", "version": "0.2.0"}}}),
            Response({"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}]}}),
        ]
        self.store.runner = lambda request, timeout=None: script.pop(0)
        chat = self.store.read("-5120690279", history_limit=5)
        self.assertEqual("userio.messenger.v1", chat.get("schema"))
        self.assertEqual(1, len(chat.get("messages") or []))

    def test_an_outside_scope_refusal_stays_a_clear_failure(self) -> None:
        import urllib.error
        refusal = urllib.error.HTTPError(
            "https://msg.example.org/mcp", 409,
            json.dumps({"error": "chat_or_account_outside_token_scope"}).encode(), {}, None)
        self.store.runner = lambda request, timeout=None: (_ for _ in ()).throw(refusal)
        with self.assertRaisesRegex(UserIOChatsError, "409"):
            self.store.read("-5120690279", history_limit=5)

    def test_a_missing_token_file_is_a_clear_configuration_failure(self) -> None:
        store = UserIOChatsStore(
            mcp_url="https://msg.example.org/mcp",
            token_file="/nonexistent/secretary-readonly-runtime.token", timeout=5)
        with self.assertRaisesRegex(UserIOChatsError, "token file is missing"):
            store.read("-5120690279", history_limit=5)


if __name__ == "__main__":
    unittest.main()
