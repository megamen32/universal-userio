#!/usr/bin/env node
// Standalone VK Inbox extension gateway.
//
// In production this lives inside the BrowserOS extension (mv3) and reaches
// back into the service-worker IndexedDB. For headless testing and local
// development it runs as a separate process that holds attachments in an
// in-memory map keyed by `peer_id:msg_id:idx`.
//
// Wire contract (matches lib/userio.js forwardCapture + adapters.VKChannelAdapter):
//   POST /vk/attachment
//   Authorization: Bearer <USERIO_API_TOKEN>
//   {"peer_id": "...", "msg_id": "...", "idx": 0, "attachment_id": "vk:sw:...:0"}
// Responds with raw bytes (image/png, etc.) or JSON {"error": "..."}.
import http from "node:http";
import { Buffer } from "node:buffer";

const PORT = Number(process.env.USERIO_VK_GATEWAY_PORT || 18098);
const TOKEN = process.env.USERIO_VK_GATEWAY_TOKEN || "";
const STATIC = new Map(); // key "peer:msg:idx" -> { content_type, filename, bytes }

function put(key, buf, contentType, filename) {
  STATIC.set(key, { content_type: contentType, filename, bytes: buf });
}

function lookup(payload) {
  const key = `${payload.peer_id || ""}:${payload.msg_id || ""}:${Number(payload.idx) || 0}`;
  return STATIC.get(key);
}

export function seedAttachment({ peer_id, msg_id, idx, content_type = "image/png", filename = "blob.png", data_b64 }) {
  const buf = Buffer.from(String(data_b64 || ""), "base64");
  put(`${peer_id}:${msg_id}:${idx}`, buf, content_type, filename);
}

const server = http.createServer((req, response) => {
  if (req.method === "GET" && req.url === "/health") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: true, entries: STATIC.size }));
    return;
  }
  if (req.method === "POST" && req.url === "/vk/attachment/seed") {
    const auth = req.headers["authorization"] || "";
    if (TOKEN && auth !== `Bearer ${TOKEN}`) {
      response.writeHead(401, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "unauthorized" }));
      return;
    }
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
        const key = `${body.peer_id || ""}:${body.msg_id || ""}:${Number(body.idx) || 0}`;
        const buf = Buffer.from(String(body.data_b64 || ""), "base64");
        STATIC.set(key, {
          content_type: body.content_type || "application/octet-stream",
          filename: body.filename || `vk-${key}`,
          bytes: buf,
        });
        response.writeHead(202, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ ok: true, key, size: buf.length }));
      } catch (error) {
        response.writeHead(400, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: `seed failed: ${error.message}` }));
      }
    });
    return;
  }
  if (req.method !== "POST" || req.url !== "/vk/attachment") {
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: "not found" }));
    return;
  }
  if (TOKEN) {
    const auth = req.headers["authorization"] || "";
    if (auth !== `Bearer ${TOKEN}`) {
      response.writeHead(401, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "unauthorized" }));
      return;
    }
  }
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    let body = {};
    try { body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch (_) {}
    const stored = lookup(body);
    if (!stored) {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: `vk attachment not found: ${body.peer_id || "?"}:${body.msg_id || "?"}:${body.idx}` }));
      return;
    }
    response.writeHead(200, {
      "Content-Type": stored.content_type,
      "Content-Disposition": `attachment; filename="${stored.filename}"`,
      "Content-Length": stored.bytes.length,
    });
    response.end(stored.bytes);
  });
});

if (import.meta.url === `file://${process.argv[1]}`) {
  server.listen(PORT, "127.0.0.1", () => {
    console.log(`[vk-gateway] listening on http://127.0.0.1:${PORT}`);
  });
}

export { server, STATIC };
