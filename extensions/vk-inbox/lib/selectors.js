// VK DOM selector strategy with multiple fallbacks.
// VK redesign 2026 uses hashed class names (vkit-XXXXX) and data-testid; older
// builds still expose .im-mess / [data-msgid]. We try all known patterns and
// return the first non-empty result.

(function (root) {
  const lib = {};

  const text = (node) =>
    (node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim();

  // Chat list container (left side / floating panel).
  lib.chatListContainer = () =>
    document.querySelector('[data-testid="me_convo_list"]') ||
    document.querySelector('[data-testid="conversation-list"]') ||
    document.querySelector(".im-page--chats") ||
    document.querySelector(".im-page--left-pane") ||
    document.querySelector("[class*=\"chats-list\"]") ||
    document.querySelector(".FCPanel__list") ||
    document.body;

  // Individual chat item. Returns Element.
  lib.chatItems = () => {
    const all = [];
    document
      .querySelectorAll(
        '[data-testid="me_convo_list"] > *, [data-testid="me_convo_list"] [role="link"], [data-testid="me_convo_list"] [role="button"]'
      )
      .forEach((n) => all.push(n));
    // VK redesign 2026: chat rows are DIV.ConvoList__item[data-itemkey]
    document
      .querySelectorAll(".ConvoList__item[data-itemkey]")
      .forEach((n) => all.push(n));
    document
      .querySelectorAll(".FCThumb")
      .forEach((n) => all.push(n));
    document.querySelectorAll(".im-chat-link, .im-page--chats-item").forEach((n) => all.push(n));
    // de-dup by element identity
    return [...new Set(all)];
  };

  // Chat title text from a chat item.
  lib.chatTitle = (item) => {
    if (!item) return "";
    const aria = item.getAttribute("aria-label");
    if (aria && aria.length > 1) return aria;
    const sub = item.querySelector(
      '[data-testid="conversation-title"], [class*="title"], [class*="name"]'
    );
    if (sub && text(sub)) return text(sub);
    return text(item);
  };

  // Chat peer id. VK stores it in several places: dataset, href ?sel=, link attr.
  lib.chatPeerId = (item) => {
    if (!item) return "";
    const ds = item.dataset || {};
    // VK redesign 2026: data-itemkey="convo_-239277144" on DIV.ConvoList__item
    if (ds.itemkey || ds.itemKey) {
      return String(ds.itemkey || ds.itemKey).replace(/^convo_/, "");
    }
    const fromDs = ds.peer || ds.peerId || ds.id || ds.convId || "";
    if (fromDs) return fromDs;
    const href = item.getAttribute && (item.getAttribute("href") || "");
    const fromHref = lib.peerIdFromUrl(href || "");
    if (fromHref) return fromHref;
    // Floating-panel thumbs carry no key either — recover the numeric id from
    // the avatar clip-path mask (e.g. #mePeerFrameOffline48Mask-239277144).
    const styled =
      item.matches && item.matches('[style*="Mask-"]')
        ? item
        : item.querySelector && item.querySelector('[style*="Mask-"]');
    const m = styled && (styled.getAttribute("style") || "").match(/Mask-(\d+)/);
    return m ? m[1] : "";
  };

  // Chat peer id from URL.
  lib.peerIdFromUrl = (href) => {
    try {
      const u = new URL(href, location.href);
      const sel = u.searchParams.get("sel");
      if (sel) return sel;
      const m = (u.pathname || "").match(/\/im\/([^/?#]+)/);
      if (m) return decodeURIComponent(m[1]);
      return "";
    } catch (e) {
      return "";
    }
  };

  // Chat preview text (last message snippet).
  lib.chatPreview = (item) => {
    if (!item) return "";
    const sub = item.querySelector(
      '[data-testid="conversation-preview"], [class*="preview"], [class*="last-message"]'
    );
    if (sub) return text(sub);
    // FCThumb shows "1\nЯна" → take the second line as preview when first line is digit
    const t = text(item);
    if (/^\d+\s/.test(t)) return t.replace(/^\d+\s*/, "");
    return t;
  };

  lib.chatUnread = (item) => {
    if (!item) return 0;
    const c = item.querySelector(
      '[data-testid="unread-counter"], [class*="UnreadCounter"], [class*="unread"]'
    );
    if (!c) return 0;
    const n = parseInt((c.innerText || "").trim(), 10);
    return Number.isFinite(n) ? n : 0;
  };

  // Active chat view (right side) — root containing the message stream.
  lib.chatViewRoot = () =>
    document.querySelector('[data-testid="me_right_panel"]') ||
    document.querySelector('[data-testid="conversation-pane"]') ||
    document.querySelector(".im-page--chat") ||
    document.querySelector(".im-page--right-pane") ||
    document.body;

  // Message nodes (any direction).
  lib.messageNodes = () => {
    const all = [];
    const push = (n) => {
      if (n && !all.includes(n)) all.push(n);
    };
    document.querySelectorAll("[data-msgid], [data-message-id]").forEach(push);
    document
      .querySelectorAll('[data-testid="message"], [data-testid="message-text"]')
      .forEach(push);
    document
      .querySelectorAll('[class*="Message"]:not([class*="MessageTab"]):not([class*="MessagesContainer"])')
      .forEach(push);
    document.querySelectorAll(".im-mess, .im-mess--text").forEach(push);
    return all;
  };

  // Body text from a message node.
  lib.messageBody = (node) => {
    if (!node) return "";
    const sub = node.querySelector(
      '[data-testid="message-text"], [class*="message-text"], .im-mess--text, .im-mess--text-w'
    );
    return text(sub || node);
  };

  // Message stable id.
  lib.messageId = (node, fallback) => {
    if (!node) return String(fallback);
    const ds = node.dataset || {};
    return String(ds.msgid || ds.messageId || ds.id || node.getAttribute("data-msgid") || node.getAttribute("data-message-id") || fallback);
  };

  // Direction: 'in' (peer wrote) / 'out' (we wrote) / null.
  lib.messageDirection = (node) => {
    if (!node) return null;
    const cls = (node.className || "") + " " + Array.from(node.classList || []).join(" ");
    if (/out(|--| )/i.test(cls)) return "out";
    if (/(in-|own)/i.test(cls)) return "in";
    const ds = node.dataset || {};
    if (ds.out === "1" || ds.out === "true") return "out";
    if (ds.out === "0" || ds.out === "false") return "in";
    return null;
  };

  // Message timestamp (ms). VK uses data-ts on older redesigns.
  lib.messageTs = (node) => {
    if (!node) return 0;
    const ts = node.getAttribute("data-ts");
    if (ts) {
      const n = parseInt(ts, 10);
      if (Number.isFinite(n)) {
        // VK ts is seconds or ms depending on build — normalize.
        return n > 1e12 ? n : n * 1000;
      }
    }
    return Date.now();
  };

  // Active peer in the chat view (URL ?sel= or breadcrumb).
  lib.activePeerId = () => {
    const sel = new URLSearchParams(location.search).get("sel");
    if (sel) return sel;
    const path = location.pathname.match(/\/im\/([^/?#]+)/);
    if (path) return decodeURIComponent(path[1]);
    return "";
  };

  lib.activePeerTitle = () => {
    const header =
      document.querySelector('[data-testid="conversation-header"]') ||
      document.querySelector(".im-page--header-main") ||
      document.querySelector(".im-page--header") ||
      document.querySelector('h1, h2');
    return text(header);
  };

  // Message input (contenteditable).
  lib.messageInput = () => {
    const candidates = document.querySelectorAll('[contenteditable="true"]');
    for (const c of candidates) {
      // exclude search inputs (top header) — they typically have a different role
      const ph = (c.getAttribute("placeholder") || "").toLowerCase();
      const aria = (c.getAttribute("aria-label") || "").toLowerCase();
      if (ph.includes("поиск") || ph.includes("search")) continue;
      if (aria.includes("поиск") || aria.includes("search")) continue;
      // right panel inputs are the chat composer
      const inRight =
        c.closest('[data-testid="me_right_panel"]') ||
        c.closest('[data-testid="conversation-pane"]') ||
        c.closest(".im-page--chat") ||
        c.closest(".im-page--right-pane");
      if (inRight) return c;
    }
    // last fallback: last contenteditable on the page (usually composer)
    return candidates[candidates.length - 1] || null;
  };

  // Send button. Returns Element or null.
  lib.sendButton = () => {
    const candidates = [
      '[aria-label="Отправить сообщение"]',
      '[aria-label="Send message"]',
      '.ConvoComposer__sendButton--submit',
      '[data-testid="send-message-button"]',
      '[data-testid="im-send-btn"]',
      '[aria-label="Отправить"]',
      '[aria-label="Send"]',
      'button[class*="SendMessage"]',
      'button[class*="send-btn"]',
      ".im-send-btn",
      // VK redesign 2026: mic and submit share the sendButton class — never
      // return the mic variant, clicking it does nothing.
      ".ConvoComposer__sendButton:not(.ConvoComposer__sendButton--mic)",
    ];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    // svg fallback: return the BUTTON wrapping the send icon, never the bare
    // svg element (click() on svg is a no-op).
    const svg = document.querySelector('button svg[class*="send_24"]');
    const wrap = svg && svg.closest("button");
    if (wrap) return wrap;
    const btns = document.querySelectorAll("button");
    for (const b of btns) {
      const t = (b.innerText || "").trim().toLowerCase();
      if (t === "отправить" || t === "send") return b;
    }
    return null;
  };

  // Insert text into a contenteditable element so VK's React bindings
  // register the change. innerHTML-style insertion is silently discarded by
  // the VK composer — execCommand('insertText') is what it accepts.
  lib.setInputText = (input, text) => {
    if (!input) return false;
    input.focus();
    input.innerHTML = "";
    const value = String(text || "");
    const lines = value.split("\n");
    lines.forEach((line, i) => {
      if (i > 0) document.execCommand("insertLineBreak");
      if (line) document.execCommand("insertText", false, line);
    });
    input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  lib.waitFor = (predicate, timeoutMs = 8000, intervalMs = 200) => {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const tick = () => {
        try {
          const r = predicate();
          if (r) return resolve(r);
        } catch (_) {}
        if (Date.now() - start > timeoutMs) return reject(new Error("waitFor timeout"));
        setTimeout(tick, intervalMs);
      };
      tick();
    });
  };

  root.VKSelectors = lib;
})(typeof window !== "undefined" ? window : self);
