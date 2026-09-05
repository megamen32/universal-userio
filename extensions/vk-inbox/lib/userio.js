// UserIO HTTP bridge. Used by the background service worker.
// Server is loopback (127.0.0.1:18093) by default — never exposed externally.

(function (root) {
  const lib = {};

  const DEFAULTS = {
    endpoint: "http://127.0.0.1:18093",
    token: "",
    source: "vk",
    routeId: "vk-browser",
  };

  lib.settings = async () => {
    const s = await chrome.storage.local.get(DEFAULTS);
    return { ...DEFAULTS, ...s };
  };

  async function call(method, path, body) {
    const s = await lib.settings();
    const url = s.endpoint.replace(/\/+$/, "") + path;
    const headers = { "Content-Type": "application/json" };
    if (s.token) headers["Authorization"] = "Bearer " + s.token;
    const res = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {}
    if (!res.ok) {
      const msg = (data && data.error) || `HTTP ${res.status}`;
      throw new Error(`UserIO ${method} ${path}: ${msg}`);
    }
    return data;
  }

  lib.call = call;

  lib.postMessage = async (message) => {
    const s = await lib.settings();
    return call("POST", "/v1/messages", { route_id: s.routeId, message });
  };

  lib.markSeen = (messageId) =>
    call("POST", "/v1/inbox/seen", { source: "vk", message_id: String(messageId) });

  lib.listInbox = () => call("GET", "/v1/inbox");

  lib.listConversations = () => call("GET", "/v1/conversations?source=vk");

  lib.createDraft = (conversationId, body) =>
    call("POST", `/v1/conversations/${encodeURIComponent(conversationId)}/drafts`, { body });

  lib.approveDraft = (draftId) =>
    call("POST", `/v1/drafts/${encodeURIComponent(draftId)}/approve`, {});

  // Convenience: forward a captured message envelope to UserIO.
  lib.forwardCapture = async ({ peerId, peerName, msgId, body, ts, direction, attachments }) => {
    const attList = Array.isArray(attachments) ? attachments : [];
    return lib.postMessage({
      schema: "universal.inbox.message.v1",
      source: "vk",
      message_id: String(msgId),
      sender: direction === "out" ? "self" : peerId,
      body: String(body || "").slice(0, 8000),
      received_at: (ts || Date.now()) / 1000,
      ...(peerName ? { display_name: peerName } : {}),
      ...(attList.length
        ? {
            attachments: attList.map((a) => ({
              // Pure metadata; bytes live in extension IDB and UserIO pulls
              // them via /v1/channels/vk/attachments/{id} on demand.
              kind: a.kind || guessAttachmentKind(a),
              content_type: a.content_type || "application/octet-stream",
              filename: a.filename || deriveFilename(a.src, a.content_type),
              size: typeof a.size === "number" ? a.size : null,
              src: a.src || null,
              attachment_id: `vk:sw:${peerId}:${msgId}:${a.idx}`,
            })),
          }
        : {}),
    });
  };

  function guessAttachmentKind(a) {
    const ct = (a && a.content_type) || "";
    if (ct.startsWith("image/")) return "image";
    if (ct.startsWith("video/")) return "video";
    if (ct.startsWith("audio/")) return "audio";
    if (ct === "application/pdf" || /\.pdf($|\?)/i.test(a && a.src || "")) return "doc";
    if (/\.(png|jpe?g|gif|webp|bmp|heic)($|\?)/i.test(a && a.src || "")) return "image";
    return "doc";
  }

  function deriveFilename(src, content_type) {
    try {
      const u = new URL(src);
      const last = u.pathname.split("/").filter(Boolean).pop() || "";
      if (last && /\.[a-z0-9]{2,5}$/i.test(last)) return decodeURIComponent(last);
    } catch (_) {}
    if (content_type && content_type.startsWith("image/")) return `image.${content_type.split("/")[1] || "bin"}`;
    return "attachment";
  }

  // UserIO calls us back when it needs the bytes for a VK attachment.
  // We reply with base64 over the SW RPC channel (lib/db.js stores ArrayBuffer).
  lib.serveAttachment = async (peer_id, msg_id, idx) => {
    // The actual stream happens server-side; here we just expose the lookup
    // helper for the popup / debug surface.
    return call("POST", `/v1/channels/vk/attachments`, {
      peer_id: String(peer_id),
      msg_id: String(msg_id),
      idx: Number(idx),
    });
  };

  root.UserIO = lib;
})(typeof self !== "undefined" ? self : this);
