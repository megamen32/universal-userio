"""Exercise the real MCP attachment path with a non-private PNG."""
import os
from pathlib import Path

import pytest
from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.contracts import ChannelFile
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.mcp_transport import json_rpc_response
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.mark.skipif(not Path(os.environ.get("USERIO_OCR_BINARY", "/opt/userio-ocr/userio-ocr")).is_file(), reason="local OCR runtime not installed")
def test_synthetic_png_ocr_via_mcp(tmp_path, monkeypatch):
    png = (Path(__file__).parent / "fixtures" / "userio-ocr.png").read_bytes()
    monkeypatch.setattr(TelegramChannelAdapter, "download_for_ai", lambda self, **kwargs: ChannelFile("synthetic.png", "image/png", png))
    store = SQLiteUserIOStore(tmp_path / "store.sqlite3")
    surface = UserIOMcpSurface(store, UserIOService(store, None, None))
    response = json_rpc_response(surface, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "userio.channels.download", "arguments": {"file_ref": "telegram:tg:synthetic", "mode": "mcp_resource"},
        "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}, "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"}},
    }})
    result = response["result"]
    assert result["structuredContent"]["file"]["extraction"] == "oar-ocr"
    assert "USERIO OCR TEST 123" in " ".join(block.get("text", "") for block in result["content"] if block["type"] == "text")
