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
      case "vk_send":
        return cmdSend(args);
      case "collect_run":
        return root.Collect.runDue();
      case "sleep":
        await sleep(Math.min(Number(args.ms) || 1000, 60000));
        return { ok: true };
      default:
        return { ok: false, error: `unknown action: ${command.action}` };
    }
  }

  root.Agent = lib;
})(typeof self !== "undefined" ? self : this);
