// Session vault: capture browser session state (cookies + localStorage),
// optionally encrypt it client-side, store it in the cloud (UserIO server or
// Yandex Disk) and restore it on another machine. The point: log in once, then
// move the whole session between computers without typing passwords again.
//
// Backends:
//   "userio"  — POST/GET /v1/vault/sessions/<name> on the configured endpoint
//               (auth via the same bearer token the extension already has);
//   "yadisk"  — Yandex Disk REST API under DISK:/apps/userio-vault/, OAuth
//               token kept in chrome.storage.local (yadiskToken).
//
// Encryption: passphrase -> PBKDF2-SHA256 (210k iters) -> AES-GCM. With an
// empty passphrase the blob is stored as plain JSON — fine for the LAN
// server-100 case, never use it with Yandex Disk.

(function (root) {
  const lib = {};
  const KDF_ITERS = 210000;
  const YADISK_DIR = "disk:/apps/userio-vault";

  const b64 = {
    encode(buf) {
      const bytes = new Uint8Array(buf);
      let s = "";
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
      }
      return btoa(s);
    },
    decode(text) {
      const raw = atob(text);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      return bytes;
    },
  };

  const b64json = (obj) => b64.encode(new TextEncoder().encode(JSON.stringify(obj)));
  const unb64json = (text) => JSON.parse(new TextDecoder().decode(b64.decode(text)));

  // --- crypto -------------------------------------------------------------

  async function deriveKey(passphrase, salt, iters) {
    const material = await crypto.subtle.importKey(
      "raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"],
    );
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations: iters, hash: "SHA-256" },
      material,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  }

  async function sealBlob(data, passphrase) {
    if (!passphrase) return { enc: "none", data };
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(passphrase, salt, KDF_ITERS);
    const ct = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      new TextEncoder().encode(JSON.stringify(data)),
    );
    return {
      enc: "aes-gcm-pbkdf2",
      kdf: "PBKDF2-SHA256",
      iters: KDF_ITERS,
      salt: b64.encode(salt),
      iv: b64.encode(iv),
      ct: b64.encode(ct),
    };
  }

  async function openBlob(blob, passphrase) {
    if (blob.enc === "none") return blob.data;
    if (blob.enc !== "aes-gcm-pbkdf2") throw new Error("unsupported encryption: " + blob.enc);
    if (!passphrase) throw new Error("passphrase required");
    const key = await deriveKey(
      passphrase, b64.decode(blob.salt), Number(blob.iters) || KDF_ITERS,
    );
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64.decode(blob.iv) },
      key,
      b64.decode(blob.ct),
    );
    return JSON.parse(new TextDecoder().decode(plain));
  }

  // --- capture ------------------------------------------------------------

  async function captureCookies(domains) {
    const all = await chrome.cookies.getAll({});
    let cookies = all;
    if (domains && domains.length) {
      const wanted = domains.map((d) => d.toLowerCase());
      cookies = all.filter((c) => wanted.some((d) =>
        c.domain.toLowerCase() === d || c.domain.toLowerCase().endsWith("." + d)));
    }
    // keep only fields chrome.cookies.set understands
    return cookies.map((c) => ({
      name: c.name, value: c.value, domain: c.domain, path: c.path,
      secure: c.secure, httpOnly: c.httpOnly, hostOnly: c.hostOnly,
      sameSite: c.sameSite, expirationDate: c.expirationDate,
      ...(c.partitionKey ? { partitionKey: c.partitionKey } : {}),
    }));
  }

  async function captureStorage(origins) {
    // Best-effort: only origins that already have an open tab can be read,
    // because localStorage is reachable only through a page context.
    const out = {};
    for (const origin of origins || []) {
      const tabs = await chrome.tabs.query({ url: origin + "/*" });
      const tab = tabs.find((t) => t.status === "complete") || tabs[0];
      if (!tab || !tab.id) continue;
      try {
        const [injection] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: () => {
            const pairs = {};
            for (let i = 0; i < localStorage.length; i += 1) {
              const k = localStorage.key(i);
              pairs[k] = localStorage.getItem(k);
            }
            return pairs;
          },
        });
        if (injection && injection.result) out[origin] = injection.result;
      } catch (_) { /* page may be gone; skip origin */ }
    }
    return out;
  }

  // --- restore ------------------------------------------------------------

  function cookieUrl(c) {
    const scheme = c.secure ? "https" : "http";
    const host = c.hostOnly ? c.domain : c.domain.replace(/^\./, "");
    return `${scheme}://${host}${c.path || "/"}`;
  }

  async function restoreCookies(cookies) {
    let restored = 0;
    const failed = [];
    for (const c of cookies || []) {
      const details = {
        url: cookieUrl(c),
        name: c.name,
        value: c.value,
        path: c.path || "/",
        secure: !!c.secure,
        httpOnly: !!c.httpOnly,
        sameSite: c.sameSite && c.sameSite !== "unspecified" ? c.sameSite : "unspecified",
      };
      if (!c.hostOnly && c.domain) details.domain = c.domain;
      if (c.expirationDate) details.expirationDate = c.expirationDate;
      if (c.partitionKey) details.partitionKey = c.partitionKey;
      try {
        await chrome.cookies.set(details);
        restored += 1;
      } catch (e) {
        failed.push({ name: c.name, domain: c.domain, error: String(e && e.message || e) });
      }
    }
    return { restored, failed };
  }

  async function restoreStorage(storageMap) {
    const restored = [];
    for (const [origin, pairs] of Object.entries(storageMap || {})) {
      let tab;
      try {
        tab = await chrome.tabs.create({ url: origin + "/favicon.ico", active: false });
        for (let i = 0; i < 20; i += 1) {
          const t = await chrome.tabs.get(tab.id).catch(() => null);
          if (t && t.status === "complete") break;
          await new Promise((r) => setTimeout(r, 300));
        }
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: (items) => {
            for (const [k, v] of Object.entries(items)) {
              try { localStorage.setItem(k, String(v)); } catch (_) {}
            }
          },
          args: [pairs],
        });
        restored.push(origin);
      } catch (_) { /* skip origin */ } finally {
        if (tab && tab.id) await chrome.tabs.remove(tab.id).catch(() => {});
      }
    }
    return restored;
  }

  // --- backends -----------------------------------------------------------

  function backendName() {
    return root.USERIO_CONFIG && root.USERIO_CONFIG.vaultBackend || "userio";
  }

  async function yadiskToken() {
    const { yadiskToken } = await chrome.storage.local.get({ yadiskToken: "" });
    return (root.USERIO_CONFIG && root.USERIO_CONFIG.yadiskToken) || yadiskToken;
  }

  async function yadiskCall(method, url, body, raw) {
    const token = await yadiskToken();
    if (!token) throw new Error("yadiskToken not set (options or storage)");
    const headers = { Authorization: "OAuth " + token };
    if (body && !raw) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { method, headers, body: body ? (raw ? body : JSON.stringify(body)) : undefined });
    if (!res.ok) throw new Error(`yadisk ${method} ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return res.json().catch(() => ({}));
  }

  const yadiskPath = (name) => `${YADISK_DIR}/${encodeURIComponent(name)}.json`;

  const backends = {
    async save(name, payload) {
      if (backendName() === "yadisk") {
        const op = await yadiskCall("GET",
          `https://cloud-api.yandex.net/v1/disk/resources/upload?path=${encodeURIComponent(yadiskPath(name))}&overwrite=true`);
        if (!op.href) throw new Error("yadisk upload href missing");
        await yadiskCall("PUT", op.href, payload, true);
        return { stored: name };
      }
      await root.UserIO.call("POST", `/v1/vault/sessions/${encodeURIComponent(name)}`, payload);
      return { stored: name };
    },
    async load(name) {
      if (backendName() === "yadisk") {
        const op = await yadiskCall("GET",
          `https://cloud-api.yandex.net/v1/disk/resources/download?path=${encodeURIComponent(yadiskPath(name))}`);
        if (!op.href) throw new Error("yadisk download href missing");
        const token = await yadiskToken();
        const res = await fetch(op.href, { headers: { Authorization: "OAuth " + token } });
        if (!res.ok) throw new Error(`yadisk blob HTTP ${res.status}`);
        return res.json();
      }
      return root.UserIO.call("GET", `/v1/vault/sessions/${encodeURIComponent(name)}`);
    },
    async list() {
      if (backendName() === "yadisk") {
        const data = await yadiskCall("GET",
          `https://cloud-api.yandex.net/v1/disk/resources?path=${encodeURIComponent(YADISK_DIR)}&limit=200`);
        const items = (data._embedded && data._embedded.items) || [];
        return items.map((it) => ({
          name: String(it.name || "").replace(/\.json$/, ""),
          enc: "unknown", machine: "", profile: "", domains: [], cookie_count: 0,
          stored_by: "", stored_at: it.modified ? Date.parse(it.modified) / 1000 : 0,
          size: it.size || 0,
        }));
      }
      const data = await root.UserIO.call("GET", "/v1/vault/sessions");
      return data.sessions || [];
    },
    async del(name) {
      if (backendName() === "yadisk") {
        await yadiskCall("DELETE",
          `https://cloud-api.yandex.net/v1/disk/resources?path=${encodeURIComponent(yadiskPath(name))}`);
        return { deleted: name };
      }
      await root.UserIO.call("DELETE", `/v1/vault/sessions/${encodeURIComponent(name)}`);
      return { deleted: name };
    },
  };

  // --- public API ---------------------------------------------------------

  lib.save = async (options = {}) => {
    const name = String(options.name || "").trim();
    if (!name) throw new Error("session name required");
    const domains = options.domains && options.domains.length
      ? options.domains.map(String) : null;
    const origins = options.origins && options.origins.length ? options.origins : [];
    const cookies = await captureCookies(domains);
    const storage = origins.length ? await captureStorage(origins) : {};
    const platform = await chrome.runtime.getPlatformInfo().catch(() => ({}));
    const data = {
      v: 1,
      captured_at: new Date().toISOString(),
      user_agent: navigator.userAgent,
      platform: platform.os || "",
      cookies,
      storage,
    };
    const blob = await sealBlob(data, options.passphrase || "");
    const payload = {
      blob,
      enc: blob.enc,
      agent: "universal-userio-agent/" + chrome.runtime.getManifest().version,
      machine: options.machine || platform.os || "unknown",
      profile: options.profile || "",
      domains: domains || [],
      cookie_count: cookies.length,
    };
    await backends.save(name, payload);
    return {
      ok: true, name, cookies: cookies.length, storage_origins: Object.keys(storage),
      enc: blob.enc, machine: payload.machine,
    };
  };

  lib.restore = async (options = {}) => {
    const name = String(options.name || "").trim();
    if (!name) throw new Error("session name required");
    const record = await backends.load(name);
    const data = await openBlob(record.blob, options.passphrase || "");
    const cookieResult = await restoreCookies(data.cookies);
    let storageOrigins = [];
    if (options.include_storage !== false && data.storage) {
      storageOrigins = await restoreStorage(data.storage);
    }
    return {
      ok: true, name,
      cookies_restored: cookieResult.restored,
      cookies_failed: cookieResult.failed,
      storage_restored: storageOrigins,
      captured_at: data.captured_at || "",
    };
  };

  lib.list = () => backends.list();
  lib.del = (name) => backends.del(String(name || "").trim());
  lib.state = async () => ({
    backend: backendName(),
    hasYadiskToken: !!(await yadiskToken()),
  });

  root.Vault = lib;
})(typeof self !== "undefined" ? self : this);
