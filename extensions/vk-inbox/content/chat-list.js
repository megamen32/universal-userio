// Real-time observer for the VK chat list.
// Runs on any vk.com / vk.ru page; filters for the chat list container and
// forwards each chat item to the background as a capture event.

(function () {
  const SEL = self.VKSelectors;
  if (!SEL) {
    console.warn("[userio-vk] selectors lib missing — content script aborted");
    return;
  }

  const SEEN = new Set();

  function pushChat(item) {
    if (!item) return;
    const peerId = SEL.chatPeerId(item) || SEL.peerIdFromUrl(item.getAttribute("href") || "");
    if (!peerId) return;
    const title = SEL.chatTitle(item);
    const preview = SEL.chatPreview(item);
    const unread = SEL.chatUnread(item);
    const key = `${peerId}::${preview}::${unread}::${title}`;
    if (SEEN.has(key)) return;
    SEEN.add(key);

    chrome.runtime.sendMessage({
      kind: "capture",
      payload: {
        peer_id: peerId,
        peer_name: title,
        msg_id: `chatlist-${peerId}-${Date.now()}-${Math.random().toString(36).slice(2,6)}`,
        body: preview || "(chat)",
        ts: Date.now(),
        direction: "in",
        unread_increment: unread,
      },
    });
  }

  function scanAll() {
    SEL.chatItems().forEach(pushChat);
  }

  function installObserver() {
    const container = SEL.chatListContainer();
    if (!container) return;
    scanAll();
    const mo = new MutationObserver((mutations) => {
      // any added/removed children may add or update chats
      for (const m of mutations) {
        m.addedNodes.forEach((n) => {
          if (n.nodeType === 1) {
            if (n.matches && n.matches('.ConvoList__item, [role="listitem"], [role="link"], [role="button"], .FCThumb')) {
              pushChat(n);
            }
            // descendants
            n.querySelectorAll && n.querySelectorAll('.ConvoList__item, [role="listitem"], [role="link"], [role="button"], .FCThumb').forEach(pushChat);
          }
        });
        if (m.type === "characterData" || m.type === "attributes") {
          // text updates (preview/unread change) — re-scan the affected subtree root
          const t = m.target;
          if (t && t.nodeType === 1) pushChat(t.closest('.ConvoList__item, [role="listitem"], [role="link"], [role="button"], .FCThumb'));
        }
      }
    });
    mo.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["class", "aria-label", "data-unread", "data-ts"],
    });
    self.__userioVkListObserver = mo;
  }

  // wait for the chat list to appear (VK is slow to mount on first load)
  SEL.waitFor(() => {
    const c = SEL.chatListContainer();
    return c && c !== document.body ? c : null;
  }, 15000)
    .then(installObserver)
    .catch(() => installObserver()); // body fallback
})();
