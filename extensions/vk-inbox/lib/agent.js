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

  const loops = new Map();
  async function runLoop() {
    for (;;) {
      try {
        chrome.runtime.getPlatformInfo(() => {});
        const connections = await root.UserIOConnections.list();
        for (const connection of connections.filter(c=>c.enabled!==false)) if (!loops.has(connection.id)) {
          const task=connectionLoop(connection.id).finally(()=>loops.delete(connection.id)); loops.set(connection.id,task);
        }
        lastOkAt = Date.now();
        lastError = "";
      } catch (e) {
        lastError = String(e && e.message || e);
        await sleep(5000);
      }
      await saveState();
      await sleep(5000);
    }
  }

  async function connectionLoop(connectionId) {
    while ((await root.UserIOConnections.get(connectionId))?.enabled) {
      try { const context=await root.UserIO.settings(connectionId); const principal=await root.UserIO.call('GET','/v1/connection-principal',null,connectionId); if(!principal?.user_id)throw new Error('principal_unavailable');
        const command=await pollOnce(connectionId); if(command)await processCommand(command,{connectionId,agentInstanceId:context.agentInstanceId,principalId:principal.user_id});
      } catch(e) { lastError=String(e?.message||e); await sleep(5000); }
    }
  }

  async function processCommand(command,context) {
    if(!command?.id || !context?.connectionId || !context?.principalId || !context?.agentInstanceId) throw new Error('invalid_command_context');
    if(command.connection_id && command.connection_id!==context.connectionId || command.agent_instance_id && command.agent_instance_id!==context.agentInstanceId || command.principal_id && command.principal_id!==context.principalId) throw new Error('command_context_mismatch');
    const key='userioCommandReceipt:'+JSON.stringify([context.connectionId,context.principalId,context.agentInstanceId,String(command.id)]);
    const prior=(await chrome.storage.local.get(key))[key]; if(prior) return {ok:false,error:'command_already_seen',phase:prior.phase};
    await chrome.storage.local.set({[key]:{phase:'started'}});
    const metadata=new Set(['ping','gpt_identity','gpt_scope','zai_scope','gpt_session_proof']);
    const allowed={gpt_list:'chatgpt',gpt_read:'chatgpt',gpt_send:'chatgpt',gpt_first_send:'chatgpt',gpt_archive:'chatgpt',zai_list:'zai',zai_read:'zai',zai_send:'zai'};
    let result;
    if(!metadata.has(command.action)) {const provider=allowed[command.action],scope=command.args?.expected_account_scope;
      const selected=provider && scope ? await root.UserIOConnections.subscriptionsForScope(provider,scope) : [];
      if(!selected.some(c=>c.id===context.connectionId)) result={ok:false,error:'account_not_authorized'};
    }
    if(!result) result=await execute(command).catch(e=>({ok:false,error:String(e?.message||e)}));
    const receipt={phase:'executed',result}; await chrome.storage.local.set({[key]:receipt}); handled++;
    await root.UserIO.call('POST','/v1/agent/results',{id:command.id,agent_id:command.agent_id,action:command.action,connection_id:context.connectionId,agent_instance_id:context.agentInstanceId,result},context.connectionId);
    await chrome.storage.local.set({[key]:{...receipt,phase:'delivered'}}); return result;
  }
  lib.processCommand=processCommand;

  async function pollOnce(connectionId) {
    const s = await root.UserIO.settings(connectionId);
    const base = s.endpoint.replace(/\/+$/, "");
    const url = `${base}/v1/agent/poll?agent_id=${encodeURIComponent(s.agentId)}&instance_id=${encodeURIComponent(s.agentInstanceId)}&connection_id=${encodeURIComponent(connectionId || "legacy-default")}&wait=${POLL_WAIT_SEC}`;
    const headers = {};
    const token = await root.UserIO.accessToken(connectionId);
    if (token) headers["Authorization"] = "Bearer " + token;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`poll HTTP ${res.status}`);
    const data = await res.json().catch(() => ({}));
    if (data && data.identity_conflict) throw new Error('agent_identity_conflict');
    const command=data && data.command;
    if (command && (command.connection_id && command.connection_id!==connectionId || command.agent_instance_id && command.agent_instance_id!==s.agentInstanceId)) return null;
    return command;
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

  function serviceForUrl(url) {
    try {
      const parsed = new URL(url);
      if (parsed.hostname === "chat.z.ai") return "zai";
      if (parsed.hostname === "chatgpt.com" && parsed.pathname.startsWith("/dots/")) return "dots";
      if (parsed.hostname === "chatgpt.com") return "chatgpt";
    } catch (_) {}
    return null;
  }

  async function ensureTab(url) {
    const service = serviceForUrl(url);
    let tab = null;
    if (service && root.UserIOTabGroups?.acquire) {
      const managed = await root.UserIOTabGroups.acquire(service,{slot:"navigate",url,active:false,retarget:true,match:t=>String(t.url||"").split("?")[0]===String(url).split("?")[0]});
      if (managed?.ok) tab = managed.tab;
      else if (managed?.error && managed.error !== "tab_groups_unavailable") return managed;
    }
    if (!tab) tab = await findTab(url.split("?")[0]);
    if (!tab) {
      tab = await chrome.tabs.create({ url, active: false });
      if (service) await root.UserIOTabGroups?.assign(service,tab.id);
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
    if (!root.UserIOScopedChatGPT?.validRef(args.chat_ref)) return {ok:false,error:"invalid_chat_ref"};
    if (!["auto","ui","api"].includes(mode)) return {ok:false,error:"invalid_mode"};
    const exact = await root.UserIOScopedChatGPT.exactTab(args.chat_ref);
    if (!exact.ok) return exact;
    const tab = exact.tab;
    // Auto must never try an uncertain API POST and then repeat via UI.
    if (mode === "ui" || mode === "auto")
      return cmdGptSendUi(tab, text, args.chat_ref, args.expected_account_scope);
    // "api" or "auto": try the in-page API first (sentinel requirements
    // token), fall back to the real composer unless mode forces api.
    const [api] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (body, chatRef, scope) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        try {
          if (location.origin !== "https://chatgpt.com" || location.pathname !== "/c/" + chatRef)
            return {ok:false,error:"chat_ref_mismatch"};
          const session = await fetch("/api/auth/session", { credentials: "include" }).then((r) => r.json());
          if (!/^[a-f0-9]{64}$/.test(scope || "") || typeof session?.user?.id !== "string" || !session.user.id)
            return {ok:false,error:"account_scope_unavailable"};
          const digest = await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:session.user.id,account_id:String(session.account?.id||"")})));
          const actual = Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("");
          if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
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
      args: [text, args.chat_ref ? String(args.chat_ref) : "", args.expected_account_scope],
    });
    const apiResult = (api && api.result) || { ok: false };
    if (apiResult.ok || mode === "api") return apiResult;
    // A failed API POST has unknown admission; never replay through the UI.
    return apiResult.ok ? apiResult : {ok:false,error:"api_submission_outcome_unknown"};
  }

  async function cmdGptSendUi(tab, text, chatRef, expectedScope = "") {
    const url = chatRef
      ? `https://chatgpt.com/c/${encodeURIComponent(String(chatRef))}`
      : "https://chatgpt.com/";
    if (!root.UserIOScopedChatGPT?.validRef(chatRef)) return {ok:false,error:"invalid_chat_ref"};
    const exact = await root.UserIOScopedChatGPT.exactTab(chatRef);
    if (!exact.ok || exact.tab.id !== tab.id) return {ok:false,error:"chat_ref_mismatch"};
    await waitForComplete(tab.id, 30000);
    await sleep(3000);
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (body, ref, scope) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        try {
          if (location.origin !== "https://chatgpt.com" || location.pathname !== "/c/" + ref)
            return {ok:false,error:"chat_ref_mismatch"};
          if(scope){
            if(!/^[a-f0-9]{64}$/.test(scope))return {ok:false,error:"invalid_account_scope"};
            const auth=await fetch("/api/auth/session",{credentials:"include"});
            if(!auth.ok)return {ok:false,error:"authentication_required"};
            const session=await auth.json();
            if(typeof session?.user?.id!=="string")return {ok:false,error:"account_scope_unavailable"};
            const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:session.user.id,account_id:String(session.account?.id||"")})));
            const actual=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
            if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
          }
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
      args: [text, chatRef, expectedScope],
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
        for(const key of Object.keys(state))if(key.startsWith('dot_draft_backup:'))delete state[key];
        return { ok: true, settings: state };
      }
      case "settings_set": {
        if(Object.keys(args.patch||{}).some(key=>key.startsWith('dot_draft_backup:')))return {ok:false,error:'reserved_setting'};
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
      case "dot_inspect": {
        const tabs = (await chrome.tabs.query({})).filter(t => t.url === args.url);
        if (!args.url || !/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url) || tabs.length !== 1)
          return {ok:false,error:"dot_tab_not_unique"};
        const [r] = await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:()=>({url:location.href,ids:[...document.querySelectorAll("article.message-row[data-message-id]")].filter(e=>!e.closest(".reply-chain-panel[role=dialog]")).map(e=>e.dataset.messageId),composers:[...document.querySelectorAll('[contenteditable="true"][data-composer-markdown]')].filter(e=>e.getClientRects().length).length, last_user_structure:(()=>{const a=[...document.querySelectorAll('article.message-row.self[data-message-id]')];const e=a.at(-1);return e?[e,...e.querySelectorAll('*')].slice(0,80).map(n=>({tag:n.tagName,classes:typeof n.className==='string'?n.className:'',text_length:(n.textContent||'').length,inner_length:(n.innerText||'').length})):[]})()})});
        return {ok:true,tab_id:tabs[0].id,...r.result};
      }
      case "dot_composer_inspect": {
        if(!root.UserIODot?.inspectComposer||typeof args.room!=="string"||!args.room||typeof args.url!=="string"||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url))return {ok:false,error:'invalid_dot_request'};
        const tabs=(await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:'dot_tab_not_unique'};
        await root.UserIOTabGroups?.assign("dots",tabs[0].id);
        const [result]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:'MAIN',func:root.UserIODot.inspectComposer,args:[args.url,args.room]});
        return result?.result||{ok:false,error:'inspection_failed'};
      }
      case "dot_draft_recover": {
        if(!root.UserIODot?.recoverDraft||typeof args.room!=="string"||!args.room||typeof args.url!=="string"||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url)||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(args.backup_id||"")||!['clear','restore'].includes(args.operation))return {ok:false,error:'invalid_dot_request'};
        if(args.confirm!==(args.operation==='clear'?'preserve_then_clear':'restore_preserved_draft'))return {ok:false,error:'confirmation_required'};
        const tabs=(await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:'dot_tab_not_unique'};
        await root.UserIOTabGroups?.assign("dots",tabs[0].id);
        const key='dot_draft_backup:'+args.backup_id, saved=(await chrome.storage.local.get(key))[key];
        if(!saved||saved.url!==args.url||saved.room!==args.room||typeof saved.text!=='string'||typeof saved.html!=='string')return {ok:false,error:'backup_not_found'};
        if((args.operation==='clear'&&!['saved','restored',undefined].includes(saved.phase))||(args.operation==='restore'&&saved.phase!=='cleared'))return {ok:false,error:'recovery_requires_review'};
        saved.phase=args.operation+'_started';await chrome.storage.local.set({[key]:saved});
        const [result]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:'MAIN',func:root.UserIODot.recoverDraft,args:[args.url,args.room,args.operation,saved.text,saved.html]});
        if(!result?.result?.ok)return {ok:false,error:result?.result?.error||'recovery_outcome_unknown'};
        saved.phase=args.operation==='clear'?'cleared':'restored';await chrome.storage.local.set({[key]:saved});
        return {ok:true,phase:saved.phase,backup_id:args.backup_id};
      }
      case "dot_draft_backup": {
        if(!root.UserIODot?.captureDraft||typeof args.room!=="string"||!args.room||typeof args.url!=="string"||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url)||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(args.backup_id||""))return {ok:false,error:"invalid_dot_request"};
        const tabs=(await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:"dot_tab_not_unique"};
        const key='dot_draft_backup:'+args.backup_id;
        if(args.purpose==='automatic'){
          const all=await chrome.storage.local.get(null);
          if(Object.entries(all).some(([k,v])=>k.startsWith('dot_draft_backup:')&&v?.purpose==='automatic'&&v.url===args.url&&v.room===args.room&&v.phase!=='restored'))return {ok:false,error:'unresolved_draft_backup'};
        }
        if((await chrome.storage.local.get(key))[key])return {ok:false,error:'backup_exists'};
        const [result]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:root.UserIODot.captureDraft,args:[args.url,args.room]});
        const draft=result?.result;
        if(!draft?.ok)return {ok:false,error:draft?.error||'dot_result_unknown'};
        if(typeof draft.text!=='string'||typeof draft.html!=='string'||!/^[a-f0-9]{64}$/.test(draft.hash||''))return {ok:false,error:'invalid_draft_snapshot'};
        const value={version:1,url:args.url,room:args.room,text:draft.text,html:draft.html,hash:draft.hash,created:Date.now(),...(args.purpose==='automatic'?{purpose:'automatic'}:{})};
        await chrome.storage.local.set({[key]:value});
        const saved=(await chrome.storage.local.get(key))[key];
        if(!saved||saved.text!==value.text||saved.html!==value.html||saved.hash!==value.hash)return {ok:false,error:'backup_verify_failed'};
        return {ok:true,backup_id:args.backup_id,hash:value.hash,length:value.text.length};
      }
      case "dot_draft_inspect": {
        if(!root.UserIODot?.inspectDraft||typeof args.room!=="string"||!args.room||typeof args.url!=="string"||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url)||!/^[a-f0-9]{64}$/.test(args.expected_hash||""))return {ok:false,error:"invalid_dot_request"};
        const tabs=(await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:"dot_tab_not_unique"};
        const [r]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:root.UserIODot.inspectDraft,args:[args.url,args.room,args.expected_hash]});
        return r?.result||{ok:false,error:"dot_result_unknown"};
      }
      case "dot_profile_probe": {
        if (!root.UserIODot || typeof args.room!=="string" || !args.room || typeof args.url!=="string" || !/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url)) return {ok:false,error:"invalid_dot_request"};
        const tabs = (await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if (!tabs.length) return {ok:true,matches:false};
        if (tabs.length!==1) return {ok:false,error:"dot_tab_not_unique"};
        const [r]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:root.UserIODot.pageAction,args:[args.url,args.room,null,null,null,[]]});
        if (!r?.result?.ok || r.result.room!==args.room || !r.result.cursor) return {ok:false,error:"dot_probe_unverified"};
        // Only return the binding check; do not persist the snapshot in the command audit.
        return {ok:true,matches:true};
      }
      case "dot_action": {
        if (!root.UserIODot || !["snapshot","send"].includes(args.operation) || typeof args.room!=="string" || !args.room || typeof args.url!=="string" || !/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url)) return {ok:false,error:"invalid_dot_request"};
        const tabs = (await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:"dot_tab_not_unique"};
        const text=args.operation==="snapshot"?null:args.text;
        if(args.operation==="send"&&(typeof text!=="string"||!text.trim()||text.length>16000))return {ok:false,error:"invalid_dot_text"};
        const [r]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:root.UserIODot.pageAction,args:[args.url,args.room,text,args.after||null,args.reply_to||null,args.entities||[]]});
        return r?.result || {ok:false,error:"dot_result_unknown"};
      }
      case "dot_media": {
        if(!root.UserIODot?.mediaAction||!["start","chunk","finish","upload","inspect","send"].includes(args.operation)||typeof args.room!=="string"||!args.room||typeof args.url!=="string"||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url))return {ok:false,error:"invalid_dot_request"};
        const tabs=(await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:"dot_tab_not_unique"};
        const [r]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:root.UserIODot.mediaAction,args:[args.url,args.room,args.operation,args]});
        return r?.result||{ok:false,error:"dot_result_unknown"};
      }
      case "dot_media_read": {
        if(!root.UserIODotRead?.pageAction||!["start","chunk","finish"].includes(args.operation)||typeof args.room!=="string"||!args.room||typeof args.url!=="string"||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(args.url))return {ok:false,error:"invalid_dot_request"};
        const tabs=(await chrome.tabs.query({})).filter(t=>t.url===args.url);
        if(tabs.length!==1)return {ok:false,error:"dot_tab_not_unique"};
        const [r]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},world:"MAIN",func:root.UserIODotRead.pageAction,args:[args.url,args.room,args.operation,args]});
        return r?.result||{ok:false,error:"dot_result_unknown"};
      }
      case "gpt_identity": {
        if (!root.ChatGPT) return { ok: false, error: "chatgpt module missing" };
        const identity=await root.ChatGPT.identity();
        return {ok:identity.ok,...(identity.error?{error:identity.error}:{}),account_id:identity.account_id||'',email:identity.email||'',name:identity.name||''};
      }
      case "gpt_session_proof": {
        if (!root.ChatGPT?.sessionProof) return {ok:false,error:"chatgpt_module_missing"};
        return root.ChatGPT.sessionProof(args.nonce);
      }
      case "gpt_capture_status": {
        return root.ChatGPT?.passiveStatus ? root.ChatGPT.passiveStatus() : {ok:false,error:'passive_capture_unavailable'};
      }
      case "gpt_register": {
        if (!root.ChatGPT) return { ok: false, error: "chatgpt module missing" };
        return root.ChatGPT.register();
      }
      case "zai_send": {
        return root.UserIOZaiSend?.send(args) ?? {ok:false,error:"zai_send_unavailable"};
      }
      case "zai_judge": {
        return root.UserIOZaiJudge?.infer(args) ?? {ok:false,error:"zai_judge_unavailable"};
      }
      case "zai_judge_status": {
        return root.UserIOZaiJudge?.status(args) ?? {ok:false,error:"zai_judge_unavailable"};
      }
      case "zai_scope": {
        return root.UserIOZai?.scope() ?? {ok:false,error:"zai_module_missing"};
      }
      case "zai_list": {
        return root.UserIOZai?.list(args) ?? {ok:false,error:"zai_module_missing"};
      }
      case "zai_read": {
        return root.UserIOZai?.read(args) ?? {ok:false,error:"zai_module_missing"};
      }
      case "gpt_scope": {
        return root.UserIOScopedChatGPT?.accountScope() ?? {ok:false,error:"scoped_chatgpt_unavailable"};
      }
      case "gpt_list": {
        return root.UserIOScopedChatGPT?.list(args) ?? {ok:false,error:"scoped_chatgpt_unavailable"};
      }
      case "gpt_read": {
        return root.UserIOScopedChatGPT?.read(args) ?? {ok:false,error:"scoped_chatgpt_unavailable"};
      }
      case "gpt_send": {
        return cmdGptSend(args);
      }
      case "gpt_first_send": {
        return root.UserIOScopedChatGPT?.firstSend(args) ?? {ok:false,error:'scoped_chatgpt_unavailable'};
      }
      case "gpt_archive": {
        return root.UserIOScopedChatGPT?.archive(args) ?? {ok:false,error:'scoped_chatgpt_unavailable'};
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
