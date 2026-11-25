"""Exercise the real MCP attachment path with a non-private PNG."""
import os
from pathlib import Path

import pytest
from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.contracts import ChannelFile, InboxMessage
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


@pytest.mark.skipif(not Path(os.environ.get("USERIO_OCR_BINARY", "/opt/userio-ocr/userio-ocr")).is_file(), reason="local OCR runtime not installed")
def test_real_ocr_is_searchable(tmp_path, monkeypatch):
    from universal_userio.search import search_messages
    png = (Path(__file__).parent / "fixtures" / "userio-ocr.png").read_bytes()
    monkeypatch.setattr(TelegramChannelAdapter, "download_for_ai", lambda self, **kwargs: ChannelFile("synthetic.png", "image/png", png))
    store = SQLiteUserIOStore(tmp_path / "store.sqlite3")
    service = UserIOService(store, None, None)
    service.receive(InboxMessage("telegram", "ocr-fixture", "test", "attachment", 1), route_id="telegram")
    store.upsert_attachment(source="telegram", message_id="ocr-fixture", attachment={"kind": "image"})
    result = UserIOMcpSurface(store, service).dispatch("userio.channels.download", {"file_ref": "telegram:ocr-fixture", "mode": "mcp_resource"})
    assert result["ok"] and result["file"]["extraction"] == "oar-ocr"
    assert search_messages(store, "USERIO OCR TEST 123", match_type="exact")["results"]


@pytest.mark.skipif(not Path(os.environ.get("USERIO_OCR_BINARY", "/opt/userio-ocr/userio-ocr")).is_file(), reason="local OCR runtime not installed")
def test_network_mcp_ocr_and_search_with_isolated_provider(tmp_path, monkeypatch):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from universal_userio.http_api import handler

    png = (Path(__file__).parent / "fixtures" / "userio-ocr.png").read_bytes()
    store = SQLiteUserIOStore(tmp_path / "isolated.sqlite3")
    service = UserIOService(store, None, None)
    service.receive(InboxMessage("telegram", "fixture-only", "test", "attachment", 1), route_id="telegram")
    store.upsert_attachment(source="telegram", message_id="fixture-only", attachment={"kind": "image"})
    def provider(self, **kwargs):
        if not getattr(provider, 'called', False):
            provider.called = True
            service.receive(InboxMessage("telegram", "unrelated-write", "test", "other", 2), route_id="telegram")
        return ChannelFile("synthetic.png", "image/png", png)
    monkeypatch.setattr(TelegramChannelAdapter, "download_for_ai", provider)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="isolated-test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    def call(name, arguments):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        req = Request(url, data=json.dumps(payload).encode(), headers={"Authorization": "Bearer isolated-test-token", "Content-Type": "application/json"})
        with urlopen(req) as response:
            return json.loads(response.read())["result"]
    try:
        skipped = call("userio.channels.download", {"file_ref": "telegram:fixture-only", "mode": "mcp_resource"})
        assert skipped["structuredContent"]["file"]["search_index"] == "skipped_concurrent_change"
        assert not call("userio.channels.search", {"query": "USERIO OCR TEST 123", "scope": "message", "mode": "text"})["structuredContent"]["results"]
        downloaded = call("userio.channels.download", {"file_ref": "telegram:fixture-only", "mode": "mcp_resource"})
        assert downloaded["structuredContent"]["file"]["search_index"] == "indexed"
        assert "USERIO OCR TEST 123" in downloaded["structuredContent"]["file"]["text"]
        found = call("userio.channels.search", {"query": "USERIO OCR TEST 123", "scope": "message", "mode": "text"})
        assert len(found["structuredContent"]["results"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
