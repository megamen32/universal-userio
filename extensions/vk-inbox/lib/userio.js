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
  lib.forwardCapture = async ({ peerId, peerName, msgId, body, ts, direction }) => {
    return lib.postMessage({
      schema: "universal.inbox.message.v1",
      source: "vk",
      message_id: String(msgId),
      sender: direction === "out" ? "self" : peerId,
      body: String(body || "").slice(0, 8000),
      received_at: (ts || Date.now()) / 1000,
      ...(peerName ? { display_name: peerName } : {}),
    });
  };

  root.UserIO = lib;
})(typeof self !== "undefined" ? self : this);
