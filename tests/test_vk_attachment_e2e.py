"""End-to-end test: VK Inbox extension attachment -> UserIO /media/raw.

Drives the full pipeline without a real browser:
  1) Stand up the Node.js VK extension gateway on a free port and seed it
     with a known PNG blob under a deterministic peer_id/msg_id/idx.
  2) Seed an InboxMessage with attachments, ingest it through UserIOService,
     which materializes a (user, conversation, message) row in SQLite and
     writes attachment rows via the store's `upsert_attachment`.
  3) Hit the HTTP handler at
        GET /v1/conversations/{id}/media/{msg}
        GET /v1/conversations/{id}/media/{msg}/raw
     using the Bearer token from `USERIO_API_TOKEN`.
  4) Assert the JSON describes the attachment, and the raw response is byte-
     identical to the seeded PNG.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.universal_userio.contracts import InboxMessage  # noqa: E402
from src.universal_userio.http_api import handler as make_handler  # noqa: E402
from src.universal_userio.service import UserIOService  # noqa: E402
from src.universal_userio.store import SQLiteUserIOStore  # noqa: E402


class _Gen:
    def suggest(self, *, conversation_id, latest_message):  # pragma: no cover - stub
        return ""


class _Outbox:
    def send_reply(self, *, route_id, conversation_id, draft_id, body):  # pragma: no cover - stub
        return "stub"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

PEER_ID = "u_peer_1"
MSG_ID = "vk-msg-9001"


def main() -> int:
    store_path = Path("/tmp/userio-vk-e2e.sqlite")
    if store_path.exists():
        store_path.unlink()

    store = SQLiteUserIOStore(store_path)
    service = UserIOService(store, _Gen(), _Outbox())

    gateway_port = _free_port()
    os.environ["USERIO_API_TOKEN"] = "vk-test-token"
    os.environ["USERIO_VK_EXTENSION_GATEWAY_URL"] = f"http://127.0.0.1:{gateway_port}"

    # Seed the gateway with the PNG (1x1 transparent) BEFORE we start the
    # HTTP server so the very first /vk/attachment request hits it.
    env = os.environ.copy()
    env["USERIO_VK_GATEWAY_PORT"] = str(gateway_port)
    env["USERIO_VK_GATEWAY_TOKEN"] = os.environ["USERIO_API_TOKEN"]
    proc = subprocess.Popen(
        [
            "node",
            str(ROOT / "scripts" / "vk_extension_gateway.mjs"),
        ],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        # Wait for /health to answer before driving the rest.
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{gateway_port}/health", timeout=0.5) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("gateway never became healthy")

        # Seed the PNG.
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{gateway_port}/_seed_test_pixel",
                # Real entry point is via the Node module's `seedAttachment`.
                # We use Node directly via a tiny one-shot to keep the gateway
                # minimal and avoid adding a seeding HTTP endpoint.
                data=b"",
                method="GET",
            ),
            timeout=0.2,
        )
    except Exception:
        pass

    # Seed via the JS module directly.
    # (kept as documentation; the live seed above already populates the gateway)
    # The earlier JS-driven seed was wrong because each Node process has its
    # own in-memory STATIC map. The HTTP /vk/attachment/seed endpoint above is
    # the only path that writes into the running server's map.

    # Now spin up the UserIO HTTP handler in-thread and drive requests.
    handler = make_handler(service, token=os.environ["USERIO_API_TOKEN"])

    class _ThreadedHTTPServer(HTTPServer):
        # The default HTTPServer is fine for serial tests; we only run one
        # request at a time anyway.
        pass

    server_port = _free_port()
    httpd = _ThreadedHTTPServer(("127.0.0.1", server_port), handler)
    server_thread = _thread = __import__("threading").Thread(
        target=httpd.serve_forever, daemon=True
    )
    server_thread.start()

    # Seed the gateway (its in-memory map is unique to the Node process we
    # spawned above). A second Node process has a fresh module load, so we
    # POST to /vk/attachment/seed on the live server.
    seed_req = urllib.request.Request(
        f"http://127.0.0.1:{gateway_port}/vk/attachment/seed",
        data=json.dumps({
            "peer_id": PEER_ID,
            "msg_id": MSG_ID,
            "idx": 0,
            "content_type": "image/png",
            "filename": "pixel.png",
            "data_b64": PNG_B64,
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['USERIO_API_TOKEN']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(seed_req, timeout=5) as r:
        assert r.status == 202, r.status

    try:
        base = f"http://127.0.0.1:{server_port}"
        # 1. POST the inbox message that carries the attachment metadata.
        forward = {
            "schema": "universal.inbox.message.v1",
            "source": "vk",
            "message_id": MSG_ID,
            "sender": PEER_ID,
            "display_name": "Test Peer",
            "body": "[image]",
            "attachments": [
                {
                    "kind": "image",
                    "content_type": "image/png",
                    "filename": "pixel.png",
                    "size": 68,
                    "idx": 0,
                    "attachment_id": f"vk:sw:{PEER_ID}:{MSG_ID}:0",
                    "src": "https://vk.com/seeded.png",
                }
            ],
        }
        req = urllib.request.Request(
            f"{base}/v1/messages",
            data=json.dumps({"route_id": "vk-browser", "message": forward}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ['USERIO_API_TOKEN']}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            accept = json.loads(r.read())
        conversation_id = accept["conversation_id"]
        assert accept["accepted"], accept

        # 2. List conversations to pick up the new one.
        req = urllib.request.Request(
            f"{base}/v1/conversations",
            headers={"Authorization": f"Bearer {os.environ['USERIO_API_TOKEN']}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            listing = json.loads(r.read())
        assert any(c["id"] == conversation_id for c in listing["conversations"]), listing

        # 3. Hit /v1/conversations/{id}/media/{msg} and check description.
        req = urllib.request.Request(
            f"{base}/v1/conversations/{conversation_id}/media/{MSG_ID}",
            headers={"Authorization": f"Bearer {os.environ['USERIO_API_TOKEN']}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            meta = json.loads(r.read())
        assert meta["available"] is True, meta
        assert meta["filename"] == "pixel.png", meta
        assert meta["content_type"] == "image/png", meta
        assert meta["size"] == 68, meta
        assert meta["attachments"] and meta["attachments"][0]["attachment_id"].endswith(":0"), meta

        # 4. Hit the /raw endpoint and check the bytes match the seeded PNG.
        req = urllib.request.Request(
            f"{base}/v1/conversations/{conversation_id}/media/{MSG_ID}/raw",
            headers={"Authorization": f"Bearer {os.environ['USERIO_API_TOKEN']}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            ct = r.headers["Content-Type"]
            cd = r.headers["Content-Disposition"]
        assert ct.split(";", 1)[0] == "image/png", ct
        assert "pixel.png" in cd, cd
        assert base64.b64encode(raw) == PNG_B64.encode(), (raw[:32], base64.b64encode(raw[:32]))

        print("OK vk-attachments-e2e")
        return 0
    finally:
        httpd.shutdown()
        server_thread.join(timeout=2)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        Path("/tmp/userio-vk-e2e.sqlite").unlink(missing_ok=True)
        # best-effort cleanup of any transient script artefacts
        for artefact in ("scripts/vk_seed_attachment.mjs",):
            candidate = ROOT / artefact
            if candidate.exists():
                candidate.unlink()


if __name__ == "__main__":
    sys.exit(main())
