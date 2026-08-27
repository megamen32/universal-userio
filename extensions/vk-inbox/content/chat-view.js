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

  function pushMessage(node) {
    if (!node) return;
    const peerId = activePeerId();
    if (!peerId) return;
    const body = SEL.messageBody(node);
    if (!body) return;
    const msgId = SEL.messageId(node, `view-${Date.now()}-${Math.random().toString(36).slice(2,6)}`);
    if (SEEN_MSGS.has(msgId)) return;
    SEEN_MSGS.add(msgId);

    chrome.runtime.sendMessage({
      kind: "capture",
      payload: {
        peer_id: peerId,
        peer_name: SEL.activePeerTitle() || peerId,
        msg_id: msgId,
        body: body.slice(0, 8000),
        ts: SEL.messageTs(node) || Date.now(),
        direction: SEL.messageDirection(node) || "in",
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
