// ChatGPT adapter client: exports this profile's ChatGPT session to UserIO so
// the server-side web adapter can read chats and deliver replies without the
// browser being online. The session cookie is HttpOnly, so only the extension
// (cookies permission) can read it — it goes straight to the UserIO endpoint
// and never through the operator-facing agent results channel.
//
// ChatGPT splits long JWTs into __Secure-next-auth.session-token plus
// .0/.1/... chunk cookies; all chunks are exported in order.

(function (root) {
  const lib = {};
  const COOKIE_PREFIX = "__Secure-next-auth.session-token";
  const ORIGIN = "https://chatgpt.com";

  async function fingerprint(identity) {
    const source = JSON.stringify({ id: identity.account_id, email: identity.email.toLowerCase() });
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
    return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function chunkOrder(name) {
    if (name === COOKIE_PREFIX) return -1;
    const match = name.slice(COOKIE_PREFIX.length).match(/^\.(\d+)$/);
    return match ? Number(match[1]) : 1000;
  }

  // Match a previously registered session without sending cookies or making
  // a ChatGPT request. Each one-use challenge produces a different digest.
  lib.sessionProof = async (nonce) => {
    if (typeof nonce !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(nonce)) return {ok:false,error:"invalid_challenge"};
    const cookies = (await chrome.cookies.getAll({domain:"chatgpt.com"}))
      .filter(c => c.name === COOKIE_PREFIX || c.name.startsWith(COOKIE_PREFIX + "."))
      .map(c => ({name:String(c.name),value:String(c.value || "")}))
      .filter(c => c.value).sort((a,b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    if (!cookies.length) return {ok:true,present:false};
    if (new Set(cookies.map(c=>c.name)).size!==cookies.length) return {ok:false,error:"ambiguous_session_cookie"};
    const bytes = new TextEncoder().encode(JSON.stringify([nonce,cookies.map(c=>[c.name,c.value])]));
    const key = await crypto.subtle.importKey("raw",bytes,{name:"HMAC",hash:"SHA-256"},false,["sign"]);
    const digest = await crypto.subtle.sign("HMAC",key,new TextEncoder().encode("userio-profile-match-v1"));
    return {ok:true,present:true,proof:Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("")};
  };

  lib.identity = async () => {
    const all = await chrome.cookies.getAll({ domain: "chatgpt.com" });
    const sessionChunks = all
      .filter((c) => c.name === COOKIE_PREFIX || c.name.startsWith(COOKIE_PREFIX + "."))
      .sort((a, b) => chunkOrder(a.name) - chunkOrder(b.name))
      .map((c) => ({ name: c.name, value: c.value }));
    if (!sessionChunks.length) {
      return { ok: false, error: "no ChatGPT session cookie in this profile (not logged in)" };
    }
    // Full jar: Cloudflare clearance (cf_clearance, __cf_bm) and OAI device
    // cookies are bound to the browser User-Agent, which we export alongside.
    const seen = new Set();
    const cookies = [];
    for (const c of all) {
      const key = c.name + "|" + c.domain + "|" + c.path;
      if (seen.has(key)) continue;
      seen.add(key);
      cookies.push({ name: c.name, value: c.value });
    }
    let email = "";
    let name = "";
    let accountId = "";
    try {
      const res = await fetch(ORIGIN + "/api/auth/session", { credentials: "include" });
      if (res.ok) {
        const session = await res.json();
        accountId = String((session.user && session.user.id) || "");
        email = String((session.user && session.user.email) || "");
        name = String((session.user && session.user.name) || "");
      }
    } catch (_) { /* offline; cookies alone are still exportable */ }
    if (!accountId && !email) return { ok: false, error: "Не удалось подтвердить аккаунт ChatGPT. Войдите в ChatGPT и повторите проверку." };
    return { ok: true, cookies, session_chunks: sessionChunks, account_id: accountId, email, name, user_agent: navigator.userAgent };
  };

  lib.describe = async () => {
    const identity = await lib.identity();
    if (!identity.ok) return identity;
    return {
      ok: true, email: identity.email, name: identity.name,
      fingerprint: await fingerprint(identity),
    };
  };

  lib.register = async ({ userInitiated = false, expectedFingerprint = "" } = {}) => {
    if (!userInitiated) {
      return { ok: false, error: "Выберите аккаунт ChatGPT и подтвердите передачу в окне расширения." };
    }
    const identity = await lib.identity();
    if (!identity.ok) return identity;
    if (!expectedFingerprint || await fingerprint(identity) !== expectedFingerprint) {
      return { ok: false, error: "Аккаунт ChatGPT изменился после выбора. Проверьте его снова." };
    }
    const platform = await chrome.runtime.getPlatformInfo().catch(() => ({}));
    const settings = await root.UserIO.settings();
    const agentId = settings.agentInstanceId || settings.agentId;
    const result = await root.UserIO.call("POST", "/v1/chatgpt/sessions", {
      cookies: identity.cookies,
      email: identity.email,
      name: identity.name,
      user_agent: identity.user_agent,
      agent_id: agentId,
      agent: "universal-userio-agent/" + chrome.runtime.getManifest().version,
      profile_hint: agentId + "@" + (platform.os || ""),
    });
    if (!result || result.accepted !== true) return { ok: false, error: "UserIO не подтвердил сохранение аккаунта." };
    return { ok: true, account_id: result.account_id || "", email: identity.email };
  };

  // --- self-pulled inbox sync: no operator push needed --------------------

  const SYNC_ALARM = "userio-gpt-sync";

  lib.start = () => {
    try {
      chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 15, delayInMinutes: 1 });
    } catch (_) {
      chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 30, delayInMinutes: 1 });
    }
    const onAlarm = (a) => {
      if (a.name === SYNC_ALARM) lib.sync().catch(() => {});
    };
    if (!chrome.alarms.onAlarm.hasListener(onAlarm)) {
      chrome.alarms.onAlarm.addListener(onAlarm);
    }
  };

  lib.sync = async () => {
    const { gptSync: state } = await chrome.storage.local.get({ gptSync: {} });
    const session = await fetch(ORIGIN + "/api/auth/session", { credentials: "include" })
      .then((r) => r.json()).catch(() => null);
    const token = String((session && session.accessToken) || "");
    if (!token) return { ok: false, error: "not logged in" };
    // Per-account source (chatgpt:<slug>) lets the dashboard filter chats
    // by the selected account; slug mirrors the server-side _slugify.
    const email = String((session.user && session.user.email) || "");
    const slug = email ? email.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[.-]+|[.-]+$/g, "").slice(0, 64) : "";
    const source = slug ? "chatgpt:" + slug : "chatgpt";
    const remote = await root.UserIO.call("GET", "/v1/chatgpt/sessions");
    if (!slug || !Array.isArray(remote.accounts) || !remote.accounts.some(account => account.slug === slug)) {
      return { ok: false, error: "Аккаунт ChatGPT не подключён к UserIO" };
    }
    const headers = { Authorization: "Bearer " + token };
    const convs = await fetch(ORIGIN + "/backend-api/conversations?offset=0&limit=10&order=updated&is_archived=false", {
      headers, credentials: "include",
    }).then((r) => r.json()).catch(() => null);
    if (!convs || !Array.isArray(convs.items)) throw new Error("Не удалось получить чаты ChatGPT");
    let forwarded = 0;
    for (const item of (convs && convs.items) || []) {
      const chatId = String(item.id || "");
      const updated = Number(item.update_time || 0);
      if (!chatId || updated <= (state[chatId] || 0)) continue;
      const conv = await fetch(ORIGIN + "/backend-api/conversation/" + chatId, {
        headers, credentials: "include",
      }).then((r) => r.json()).catch(() => null);
      if (!conv || !conv.mapping) throw new Error("Не удалось получить историю чата ChatGPT");
      let best = null;
      for (const node of Object.values((conv && conv.mapping) || {})) {
        const m = node && node.message;
        if (!m || !m.author || m.author.role === "system") continue;
        const text = ((m.content && m.content.parts) || [])
          .filter((part) => typeof part === "string").join(" ").trim();
        if (!text) continue;
        const ts = Number(m.create_time || 0);
        if (!best || ts >= best.ts) best = { role: m.author.role, text, ts };
      }
      if (best) {
        await root.UserIO.call("POST", "/v1/messages", {
          route_id: "chatgpt",
          message: {
            schema: "universal.inbox.message.v1",
            source,
            message_id: `${chatId}:${best.ts}`,
            sender: chatId,
            sender_name: String(item.title || "ChatGPT").slice(0, 80),
            body: `[${best.role}] ${best.text}`.slice(0, 8000),
            received_at: best.ts,
          },
        });
        forwarded += 1;
      }
      state[chatId] = updated;
    }
    await chrome.storage.local.set({ gptSync: state, gptSyncReport: { email, completedAt: Date.now() } });
    return { ok: true, forwarded };
  };

  root.ChatGPT = lib;
})(typeof self !== "undefined" ? self : this);
