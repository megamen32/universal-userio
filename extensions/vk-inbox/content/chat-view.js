// Real-time observer for an open VK chat. Captures new messages and exposes a
// sendText bridge to the background (used when the popup wants to send a
// reply through the existing VK session).

(function () {
  const SEL = self.VKSelectors;
  if (!SEL) return;

  const SEEN_MSGS = new Set();

  function activePeerId() {
    return SEL.activePeerId();
  }

  // Extract any VK-side media (photos, videos, documents, voice) embedded
  // inside a message node. Returns a list of {kind, src, content_type} that
  // the background service worker will download to bytes.
  function localAttachments(node) {
    if (!node) return [];
    const out = [];
    const imgs = node.querySelectorAll("img");
    for (const img of imgs) {
      const src = img.currentSrc || img.src;
      if (!src || src.startsWith("data:")) continue;
      // VK wraps user-uploaded photos in img with class "im_msg_image" or
      // a "photo" hint; everything else (stickers, emoji) is ignored.
      const cls = (img.className || "") + " " + (img.getAttribute("alt") || "");
      if (!/photo|attach|media|doc/i.test(cls)) continue;
      out.push({ kind: "image", src, content_type: /\.(png|jpe?g|webp|gif)(\?|$)/i.test(src) ? guessImageType(src) : "image/jpeg", filename: deriveFilename(src, "photo") });
    }
    const videos = node.querySelectorAll("video");
    for (const v of videos) {
      const src = v.currentSrc || v.src;
      if (!src || src.startsWith("data:")) continue;
      out.push({ kind: "video", src, content_type: "video/mp4", filename: deriveFilename(src, "video") });
    }
    const audios = node.querySelectorAll("audio");
    for (const a of audios) {
      const src = a.currentSrc || a.src;
      if (!src || src.startsWith("data:")) continue;
      out.push({ kind: "audio", src, content_type: "audio/mpeg", filename: deriveFilename(src, "voice") });
    }
    const docs = node.querySelectorAll('a[href*=".doc"], a[href*=".pdf"], a[href*=".zip"], a[href*=".xls"]');
    for (const a of docs) {
      const src = a.href;
      if (!src || src.startsWith("data:")) continue;
      out.push({ kind: "document", src, content_type: "application/octet-stream", filename: deriveFilename(src, "document") });
    }
    return out;
  }

  function deriveFilename(src, fallback) {
    try {
      const u = new URL(src, location.href);
      const last = u.pathname.split("/").filter(Boolean).pop() || fallback;
      return last;
    } catch { return fallback; }
  }

  function guessImageType(src) {
    const m = /\.([a-z0-9]+)(?:\?|$)/i.exec(src);
    if (!m) return "image/jpeg";
    switch (m[1].toLowerCase()) {
      case "png": return "image/png";
      case "gif": return "image/gif";
      case "webp": return "image/webp";
      default: return "image/jpeg";
    }
  }

  function pushMessage(node) {
    if (!node) return;
    const peerId = activePeerId();
    if (!peerId) return;
    const body = SEL.messageBody(node);
    if (!body) return;
    const msgId = SEL.messageId(node, `view-${Date.now()}-${Math.random().toString(36).slice(2,6)}`);
    if (SEEN_MSGS.has(msgId)) return;
    SEEN_MSGS.add(msgId);

    const attachments = localAttachments(node);
    chrome.runtime.sendMessage({
      kind: "capture",
      payload: {
        peer_id: peerId,
        peer_name: SEL.activePeerTitle() || peerId,
        msg_id: msgId,
        body: body.slice(0, 8000),
        ts: SEL.messageTs(node) || Date.now(),
        direction: SEL.messageDirection(node) || "in",
        attachments,
      },
    });
  }

  function installObserver() {
    const root = SEL.chatViewRoot();
    if (!root) return;
    // capture existing
    SEL.messageNodes().forEach(pushMessage);
    const mo = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach((n) => {
          if (n.nodeType === 1) {
            if (n.matches && (n.matches('[data-msgid], [data-message-id], .im-mess') || (n.className || "").includes("Message"))) {
              pushMessage(n);
            }
            n.querySelectorAll && n.querySelectorAll('[data-msgid], [data-message-id], .im-mess').forEach(pushMessage);
          }
        });
      }
    });
    mo.observe(root, { childList: true, subtree: true });
    self.__userioVkViewObserver = mo;
  }

  // wait for the message stream to appear
  SEL.waitFor(() => {
    const r = SEL.chatViewRoot();
    return r && r.querySelector('[contenteditable="true"], .im-page--chat, [data-testid="me_right_panel"]') ? r : null;
  }, 15000)
    .then(installObserver)
    .catch(() => installObserver());

  // sendText bridge: type body into composer and click send
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.kind !== "sendText") return false;
    (async () => {
      try {
        // ensure we are on the right chat
        if (msg.peer_id && activePeerId() !== String(msg.peer_id)) {
          // navigate
          location.href = `https://${location.host}/im?sel=${encodeURIComponent(msg.peer_id)}`;
          sendResponse({ ok: false, error: "navigating to peer — retry after load" });
          return;
        }
        const input = await SEL.waitFor(() => SEL.messageInput(), 8000).catch(() => null);
        if (!input) {
          sendResponse({ ok: false, error: "input not found" });
          return;
        }
        SEL.setInputText(input, msg.body || "");
        // small wait for VK to register change
        await new Promise((r) => setTimeout(r, 200));
        const sendBtn = await SEL.waitFor(() => SEL.sendButton(), 5000).catch(() => null);
        if (sendBtn) {
          sendBtn.click();
        } else {
          // fall back to keyboard Enter
          input.focus();
          input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
        }
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e) });
      }
    })();
    return true;
  });
})();
