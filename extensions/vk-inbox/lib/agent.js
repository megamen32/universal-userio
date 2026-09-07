// MV3 command channel. Long-polls UserIO for operator commands and executes
// them inside the real logged-in browser session; results go back to the same
// endpoint. This is how the operator drives the browser without CDP tunnels:
// the extension itself is the agent, the transport is plain HTTPS long-poll.
//
// Service-worker lifetime: each loop iteration touches an extension API
// (getPlatformInfo) which resets the MV3 idle timer, and a 30s alarm restarts
// the loop if Chrome kills the worker mid-poll.

(function (root) {
  const lib = {};
  const POLL_ALARM = "userio-agent-poll";
  const POLL_WAIT_SEC = 20;

  let running = false;
  let lastOkAt = 0;
  let lastError = "";
  let handled = 0;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function watchdog(alarm) {
    if (alarm.name === POLL_ALARM) lib.kick();
  }

  lib.start = () => {
    // 0.5 min is honored for unpacked/dev extensions; guard the call because
    // packed releases clamp the period to 1 minute.
    try {
      chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5, delayInMinutes: 0.05 });
    } catch (_) {
      chrome.alarms.create(POLL_ALARM, { periodInMinutes: 1, delayInMinutes: 0.05 });
    }
    if (!chrome.alarms.onAlarm.hasListener(watchdog)) {
      chrome.alarms.onAlarm.addListener(watchdog);
    }
    lib.kick();
  };

  lib.kick = () => {
    if (running) return;
    running = true;
    runLoop()
      .catch((e) => { lastError = String(e && e.message || e); })
      .finally(() => { running = false; });
  };

  async function runLoop() {
    for (;;) {
      try {
        chrome.runtime.getPlatformInfo(() => {});
        const command = await pollOnce();
        if (command) {
          const result = await execute(command)
            .catch((e) => ({ ok: false, error: String(e && e.message || e) }));
          handled += 1;
          await root.UserIO.call("POST", "/v1/agent/results", {
            id: command.id,
            agent_id: command.agent_id,
            action: command.action,
            result,
          });
        }
        lastOkAt = Date.now();
        lastError = "";
      } catch (e) {
        lastError = String(e && e.message || e);
        await sleep(5000);
      }
      await saveState();
    }
  }

  async function pollOnce() {
    const s = await root.UserIO.settings();
    const base = s.endpoint.replace(/\/+$/, "");
    const url = `${base}/v1/agent/poll?agent_id=${encodeURIComponent(s.agentId)}&wait=${POLL_WAIT_SEC}`;
    const headers = {};
    if (s.token) headers["Authorization"] = "Bearer " + s.token;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`poll HTTP ${res.status}`);
    const data = await res.json().catch(() => ({}));
    return data && data.command;
  }

  async function saveState() {
    try {
      await chrome.storage.local.set({
        agentState: { lastOkAt, lastError, handled, ts: Date.now() },
      });
    } catch (_) {}
  }

  lib.state = async () => ({
    ...(await chrome.storage.local.get({ agentState: {} })).agentState,
    running, lastOkAt, lastError, handled,
  });

  // --- tab helpers -------------------------------------------------------

  async function findTab(prefix) {
    const tabs = await chrome.tabs.query({});
    return tabs.find((t) => (t.url || "").startsWith(prefix)) || null;
  }

  async function ensureTab(url) {
    let tab = await findTab(url.split("?")[0]);
    if (!tab) {
      tab = await chrome.tabs.create({ url, active: false });
    } else if (tab.url !== url) {
      await chrome.tabs.update(tab.id, { url, active: true });
    } else {
      await chrome.tabs.update(tab.id, { active: true });
    }
    await waitForComplete(tab.id, 25000);
    const fresh = await chrome.tabs.get(tab.id).catch(() => null);
    return fresh || tab;
  }

  async function waitForComplete(tabId, timeoutMs) {
    const started = Date.now();
    for (;;) {
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (!tab) return;
      if (tab.status === "complete") return;
      if (Date.now() - started > timeoutMs) return;
      await sleep(500);
    }
  }

  // --- command handlers --------------------------------------------------

  async function cmdEval(args) {
    const prefix = args.url_prefix || "https://vk.";
    const tab = args.tab_id
      ? await chrome.tabs.get(Number(args.tab_id)).catch(() => null)
      : await findTab(prefix);
    if (!tab) return { ok: false, error: `no tab matching ${prefix}` };
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      world: args.world === "isolated" ? "ISOLATED" : "MAIN",
      func: async (expression) => {
        try {
          const value = await eval(expression);
          return { ok: true, value: value === undefined ? null : JSON.parse(JSON.stringify(value)) };
        } catch (e) {
          return { ok: false, error: String(e && e.message || e) };
        }
      },
      args: [String(args.expression || "1")],
    });
    return (injection && injection.result) || { ok: false, error: "no injection result" };
  }

  async function cmdNavigate(args) {
    if (!args.url) return { ok: false, error: "url required" };
    const tab = await ensureTab(String(args.url));
    return { ok: true, tab_id: tab.id, url: tab.url, title: tab.title || "" };
  }

  async function cmdSend(args) {
    if (typeof root.sendViaVK !== "function") return { ok: false, error: "sendViaVK unavailable" };
    return root.sendViaVK(String(args.peer_id || ""), String(args.body || ""));
  }

  // Serve one VK attachment's bytes by locator (peer, msg, idx) straight from
  // the extension's IndexedDB — the server-side VK adapter calls this through
  // the command channel when a user downloads media.
  async function cmdVkAttachmentBytes(args) {
    if (!root.UserIODB || !root.UserIODB.getAttachmentByIndex) {
      return { ok: false, error: "db getter unavailable" };
    }
    const row = await root.UserIODB.getAttachmentByIndex(
      String(args.peer_id || ""), String(args.msg_id || ""), Number(args.idx || 0),
    );
    if (!row) return { ok: false, error: "attachment not found in this profile" };
    if (row.status !== "ok" || !row.bytes) {
      return { ok: false, error: row.error || "attachment bytes missing", status: row.status };
    }
    const bytes = new Uint8Array(row.bytes);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return {
      ok: true,
      content_type: row.content_type || "application/octet-stream",
      filename: row.filename || "",
      size: row.size || bytes.length,
      bytes_base64: btoa(binary),
    };
  }

  async function cmdGetAttachment(args) {
    const row = await root.UserIODB.getAttachment(Number(args.id));
    if (!row) return { ok: false, error: "attachment not found" };
    const bytes = new Uint8Array(row.bytes || 0);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return {
      ok: row.status === "ok",
      id: row.id, peer_id: row.peer_id, msg_id: row.msg_id, idx: row.idx,
      content_type: row.content_type, filename: row.filename,
      size: row.size, status: row.status, error: row.error || null,
      bytes_base64: row.status === "ok" ? btoa(binary) : null,
    };
  }

  // Deliver a ChatGPT message through the REAL composer UI. The app's own
  // pipeline (sentinel tokens, proof-of-work, Cloudflare) runs as usual —
  // we only type into #prompt-textarea and press Send, like a human.
  async function cmdGptSend(args) {
    const text = String(args.text || "");
    if (!text) return { ok: false, error: "text required" };
    const mode = String(args.mode || "auto");
    let tab = await findTab("https://chatgpt.com");
    if (!tab) {
      tab = await chrome.tabs.create({ url: "https://chatgpt.com/", active: false });
      await waitForComplete(tab.id, 30000);
      await sleep(3000);
    }
    if (mode === "ui") return cmdGptSendUi(tab, text, args.chat_ref);
    // "api" or "auto": try the in-page API first (sentinel requirements
    // token), fall back to the real composer unless mode forces api.
    const [api] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (body, chatRef) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        try {
          const session = await fetch("/api/auth/session", { credentials: "include" }).then((r) => r.json());
          const token = String(session.accessToken || "");
          if (!token) return { ok: false, error: "no accessToken (logged out?)", status: 401 };
          const did = (document.cookie.match(/oai-did=([^;]+)/) || [])[1];
          const baseHeaders = {
            "Content-Type": "application/json",
            Authorization: "Bearer " + token,
            "OAI-Language": "en-US",
            ...(did ? { "OAI-Device-Id": did } : {}),
          };
          const req = await fetch("/backend-api/sentinel/chat-requirements", {
            method: "POST", credentials: "include", headers: baseHeaders,
          }).then((r) => r.json()).catch(() => null);
          const payload = {
            action: "next",
            messages: [{
              id: crypto.randomUUID(),
              author: { role: "user" },
              content: { content_type: "text", parts: [body] },
            }],
            model: "auto",
            parent_message_id: crypto.randomUUID(),
          };
          if (chatRef) {
            payload.conversation_id = chatRef;
            const prev = await fetch(`/backend-api/conversation/${chatRef}`, {
              credentials: "include", headers: { Authorization: "Bearer " + token },
            }).then((r) => r.json()).catch(() => null);
            let created = -1, parent = null;
            for (const [nodeId, node] of Object.entries((prev && prev.mapping) || {})) {
              const m = node && node.message;
              if (!m || !m.author || m.author.role === "system") continue;
              const ts = Number(m.create_time || 0);
              if (ts >= created) { created = ts; parent = nodeId; }
            }
            if (parent) payload.parent_message_id = parent;
          }
          const headers = { ...baseHeaders };
          if (req && req.token) headers["OpenAI-Sentinel-Chat-Requirements"] = req.token;
          const res = await fetch("/backend-api/conversation", {
            method: "POST", credentials: "include", headers, body: JSON.stringify(payload),
          });
          if (!res.ok) return { ok: false, error: `conversation POST HTTP ${res.status}`, status: res.status };
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "", assistantId = "", reply = "", finished = false;
          const deadline = Date.now() + 60000;
          while (!finished && Date.now() < deadline) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            for (const line of buffer.split("\n")) {
              const trimmed = line.trim();
              if (!trimmed.startsWith("data: ") || trimmed === "data: [DONE]") continue;
              try {
                const event = JSON.parse(trimmed.slice(6));
                if (event.message && event.message.author && event.message.author.role === "assistant") {
                  assistantId = event.message.id || assistantId;
                  const parts = (event.message.content && event.message.content.parts) || [];
                  const text2 = parts.filter((p) => typeof p === "string").join("");
                  if (text2) reply = text2;
                  if (event.message.status === "finished_successfully") finished = true;
                }
              } catch (_) {}
            }
            buffer = buffer.slice(buffer.lastIndexOf("\n") + 1);
          }
          return { ok: true, transport: "api", assistant_id: assistantId, reply: reply.slice(0, 4000), chat_ref: chatRef || null };
        } catch (e) {
          return { ok: false, error: String(e && e.message || e) };
        }
      },
      args: [text, args.chat_ref ? String(args.chat_ref) : ""],
    });
    const apiResult = (api && api.result) || { ok: false };
    if (apiResult.ok || mode === "api") return apiResult;
    // auto: API was rejected (Cloudflare/sentinel) — native composer.
    const ui = await cmdGptSendUi(tab, text, args.chat_ref);
    return { ...ui, transport: "ui", api_error: apiResult.error || null };
  }

  async function cmdGptSendUi(tab, text, chatRef) {
    const url = chatRef
      ? `https://chatgpt.com/c/${encodeURIComponent(String(chatRef))}`
      : "https://chatgpt.com/";
    if (!tab.url.startsWith(url.split("?")[0])) {
      await chrome.tabs.update(tab.id, { url });
    }
    await waitForComplete(tab.id, 30000);
    await sleep(3000);
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (body) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        try {
          let composer = null;
          for (let i = 0; i < 40; i += 1) {
            composer = document.querySelector("#prompt-textarea");
            if (composer) break;
            await sleep(500);
          }
          if (!composer) return { ok: false, error: "composer not found (logged out?)" };
          composer.focus();
          document.execCommand("insertText", false, body);
          await sleep(500);
          const before = document.querySelectorAll('[data-message-author-role="assistant"]').length;
          let send = document.querySelector('[data-testid="send-button"]');
          if (!send) {
            composer.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
          } else {
            send.click();
          }
          let lastText = "";
          let stable = 0;
          for (let i = 0; i < 120; i += 1) {
            await sleep(1000);
            const replies = document.querySelectorAll('[data-message-author-role="assistant"]');
            if (replies.length <= before) continue;  // only the NEW reply counts
            const last = replies[replies.length - 1];
            const t = last ? (last.textContent || "").trim() : "";
            if (t && t === lastText) {
              stable += 1;
              if (stable >= 3) break;
            } else {
              stable = 0;
              lastText = t;
            }
          }
          if (!lastText) return { ok: false, error: "no assistant reply detected" };
          return { ok: true, reply: lastText.slice(0, 4000), chat_url: location.href };
        } catch (e) {
          return { ok: false, error: String(e && e.message || e) };
        }
      },
      args: [text],
    });
    return (injection && injection.result) || { ok: false, error: "no injection result" };
  }

  async function cmdGptSendLegacyUnused(args) {
    const text = String(args.text || "");
    if (!text) return { ok: false, error: "text required" };
    const url = args.chat_ref
      ? `https://chatgpt.com/c/${encodeURIComponent(String(args.chat_ref))}`
      : "https://chatgpt.com/";
    let tab = await findTab("https://chatgpt.com");
    if (!tab) {
      tab = await chrome.tabs.create({ url, active: false });
    } else if (!tab.url.startsWith(url.split("?")[0])) {
      await chrome.tabs.update(tab.id, { url });
    }
    await waitForComplete(tab.id, 30000);
    await sleep(3000);
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (body) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        try {
          let composer = null;
          for (let i = 0; i < 40; i += 1) {
            composer = document.querySelector("#prompt-textarea");
            if (composer) break;
            await sleep(500);
          }
          if (!composer) return { ok: false, error: "composer not found (logged out?)" };
          composer.focus();
          document.execCommand("insertText", false, body);
          await sleep(500);
          let send = document.querySelector('[data-testid="send-button"]');
          if (!send) {
            composer.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
          } else {
            send.click();
          }
          let lastText = "";
          let stable = 0;
          for (let i = 0; i < 120; i += 1) {
            await sleep(1000);
            const replies = document.querySelectorAll('[data-message-author-role="assistant"]');
            const last = replies[replies.length - 1];
            const t = last ? (last.textContent || "").trim() : "";
            if (t && t === lastText) {
              stable += 1;
              if (stable >= 3) break;
            } else {
              stable = 0;
              lastText = t;
            }
          }
          if (!lastText) return { ok: false, error: "no assistant reply detected" };
          return { ok: true, reply: lastText.slice(0, 4000), chat_url: location.href };
        } catch (e) {
          return { ok: false, error: String(e && e.message || e) };
        }
      },
      args: [text],
    });
    return (injection && injection.result) || { ok: false, error: "no injection result" };
  }

  async function execute(command) {
    const args = command.args || {};
    switch (command.action) {
      case "ping": {
        const tabs = await chrome.tabs.query({ url: ["https://vk.com/*", "https://vk.ru/*"] });
        return {
          ok: true,
          agent: "universal-userio-agent/" + chrome.runtime.getManifest().version,
          manifest: chrome.runtime.getManifest().manifest_version,
          vk_tabs: tabs.map((t) => ({ id: t.id, url: t.url })),
        };
      }
      case "navigate":
        return cmdNavigate(args);
      case "eval":
        return cmdEval(args);
      case "tabs_list": {
        const tabs = await chrome.tabs.query({});
        return { ok: true, tabs: tabs.map((t) => ({ id: t.id, url: t.url, title: t.title })) };
      }
      case "close_tab": {
        await chrome.tabs.remove(Number(args.tab_id));
        return { ok: true };
      }
      case "settings_get": {
        const state = await chrome.storage.local.get(null);
        return { ok: true, settings: state };
      }
      case "settings_set": {
        await chrome.storage.local.set(args.patch || {});
        return { ok: true };
      }
      case "db_stats":
        return { ok: true, stats: await root.UserIODB.stats() };
      case "db_list_chats":
        return { ok: true, chats: await root.UserIODB.listChats() };
      case "db_list_messages":
        return { ok: true, messages: await root.UserIODB.listMessages(String(args.peer_id || "")) };
      case "db_get_attachment":
        return cmdGetAttachment(args);
      case "vk_attachment_bytes":
        return cmdVkAttachmentBytes(args);
      case "vk_send":
        return cmdSend(args);
      case "collect_run":
        return root.Collect.runDue();
      case "vault_save": {
        if (!root.Vault) return { ok: false, error: "vault module missing" };
        return root.Vault.save({
          name: String(args.name || ""),
          passphrase: args.passphrase ? String(args.passphrase) : "",
          machine: args.machine ? String(args.machine) : "",
          domains: Array.isArray(args.domains) ? args.domains : null,
          origins: Array.isArray(args.origins) ? args.origins : [],
        });
      }
      case "vault_restore": {
        if (!root.Vault) return { ok: false, error: "vault module missing" };
        const restored = await root.Vault.restore({
          name: String(args.name || ""),
          passphrase: args.passphrase ? String(args.passphrase) : "",
          include_storage: args.include_storage !== false,
        });
        if (args.open) {
          const tab = await ensureTab(String(args.open));
          restored.opened = { tab_id: tab.id, url: tab.url, title: tab.title || "" };
        }
        return restored;
      }
      case "vault_list": {
        if (!root.Vault) return { ok: false, error: "vault module missing" };
        return { ok: true, sessions: await root.Vault.list() };
      }
      case "vault_delete": {
        if (!root.Vault) return { ok: false, error: "vault module missing" };
        return root.Vault.del(String(args.name || ""));
      }
      case "gpt_identity": {
        if (!root.ChatGPT) return { ok: false, error: "chatgpt module missing" };
        return root.ChatGPT.identity();
      }
      case "gpt_register": {
        if (!root.ChatGPT) return { ok: false, error: "chatgpt module missing" };
        return root.ChatGPT.register();
      }
      case "gpt_send": {
        return cmdGptSend(args);
      }
      case "gpt_sync": {
        if (!root.ChatGPT) return { ok: false, error: "chatgpt module missing" };
        return root.ChatGPT.sync();
      }
      case "sleep":
        await sleep(Math.min(Number(args.ms) || 1000, 60000));
        return { ok: true };
      default:
        return { ok: false, error: `unknown action: ${command.action}` };
    }
  }

  root.Agent = lib;
})(typeof self !== "undefined" ? self : this);
