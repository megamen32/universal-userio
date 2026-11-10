// Public OAuth client for the Chrome extension. No browser credentials or
// site cookies are read here; the user completes UserIO's own consent screen.
(function (root) {
  const TOKEN_KEYS = ["oauthAccessToken", "oauthRefreshToken", "oauthExpiresAt", "oauthClientId", "oauthRedirectUri", "oauthEndpoint"];
  const suffix = (id) => id && id !== "legacy-default" ? ":" + id : "";
  const keys = (id) => TOKEN_KEYS.map((key) => key + suffix(id));
  const connection = async (id) => id && root.UserIOConnections ? root.UserIOConnections.get(id) : null;

  function randomVerifier() {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  async function challenge(verifier) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    return btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  async function formPost(endpoint, path, fields) {
    const response = await fetch(endpoint.replace(/\/+$/, "") + path, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(fields),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error_description || data.error || `HTTP ${response.status}`);
    return data;
  }

  async function endpoint(connectionId) {
    const configured = root.USERIO_CONFIG || {};
    const c = await connection(connectionId);
    if (connectionId && connectionId !== "legacy-default" && !c) throw new Error("unknown_connection");
    const values = await chrome.storage.local.get({ endpoint: configured.endpoint || "http://127.0.0.1:18093" });
    const value = String(c?.endpoint || values.endpoint || configured.endpoint || "http://127.0.0.1:18093").replace(/\/+$/, "");
    const parsed = new URL(value);
    if (!new Set(["http:", "https:"]).has(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error("Укажите корректный HTTP(S) адрес UserIO без логина, пароля и параметров.");
    }
    if (parsed.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname)) {
      throw new Error("Для удалённого UserIO требуется HTTPS.");
    }
    return value;
  }

  async function clientFor(base, connectionId) {
    const redirectUri = chrome.identity.getRedirectURL("userio");
    const allKeys = keys(connectionId), clientKey=allKeys[3], redirectKey=allKeys[4], endpointKey=allKeys[5];
    const existing = await chrome.storage.local.get([clientKey,redirectKey,endpointKey]);
    if (existing[clientKey] && existing[redirectKey] === redirectUri && existing[endpointKey] === base) {
      return { clientId: existing[clientKey], redirectUri };
    }
    const response = await fetch(base + "/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_uris: [redirectUri], token_endpoint_auth_method: "none" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.client_id) throw new Error(data.error || `OAuth client registration failed (HTTP ${response.status})`);
    await chrome.storage.local.set({ [clientKey]: data.client_id, [redirectKey]: redirectUri, [endpointKey]: base });
    return { clientId: data.client_id, redirectUri };
  }

  async function saveTokens(data, connectionId) {
    if (!data.access_token || !data.refresh_token || data.token_type !== "Bearer") {
      throw new Error("UserIO returned an incomplete OAuth token response");
    }
    const allKeys=keys(connectionId),a=allKeys[0],r=allKeys[1],e=allKeys[2],endpointKey=allKeys[5]; await chrome.storage.local.set({[a]:data.access_token,[r]:data.refresh_token,[e]:Date.now()+Math.max(60,Number(data.expires_in)||3600)*1000,[endpointKey]:await endpoint(connectionId)});
  }

  const refreshInFlight = new Map();
  async function accessToken(connectionId) {
    const [a,r,e,c,d,endpointKey]=keys(connectionId), values = await chrome.storage.local.get([a,r,e,c,d,endpointKey]);
    if (!values[a]) return "";
    if (values[endpointKey] !== await endpoint(connectionId)) {
      await chrome.storage.local.remove(keys(connectionId));
      return "";
    }
    if (Number(values[e]) > Date.now() + 60_000) return values[a];
    if (!values[r] || !values[c]) throw new Error("Сеанс UserIO истёк: подключите аккаунт снова.");
    if (!refreshInFlight.has(connectionId || "legacy")) {
      refreshInFlight.set(connectionId || "legacy", (async () => {
        const data = await formPost(await endpoint(connectionId), "/token", {
          grant_type: "refresh_token", client_id: values[c], refresh_token: values[r],
        });
        await saveTokens(data, connectionId);
        return data.access_token;
      })().finally(() => { refreshInFlight.delete(connectionId || "legacy"); }));
    }
    return refreshInFlight.get(connectionId || "legacy");
  }

  async function readback(token, connectionId) {
    const base = await endpoint(connectionId);
    const response = await fetch(base + "/v1/inbox", { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error(`UserIO login completed but account readback failed (HTTP ${response.status})`);
    await response.json().catch(() => ({}));
    const profile = await fetch(base + "/v1/connection-principal", { headers: { Authorization: `Bearer ${token}` } });
    if (!profile.ok) return "";
    const info = await profile.json().catch(() => ({}));
    return String(info.username || "");
  }

  async function connect(connectionId) {
    const base = await endpoint(connectionId);
    const { clientId, redirectUri } = await clientFor(base, connectionId);
    const verifier = randomVerifier();
    const state = randomVerifier();
    const params = new URLSearchParams({
      response_type: "code", client_id: clientId, redirect_uri: redirectUri,
      scope: "userio", state, code_challenge: await challenge(verifier), code_challenge_method: "S256",
    });
    const callback = await chrome.identity.launchWebAuthFlow({
      url: `${base}/authorize?${params}`,
      interactive: true,
    });
    const result = new URL(callback);
    if (result.origin + result.pathname !== redirectUri || result.searchParams.get("state") !== state) {
      throw new Error("Ответ входа UserIO не совпал с этим запросом. Повторите подключение.");
    }
    const code = result.searchParams.get("code");
    if (!code) throw new Error("UserIO не вернул код авторизации.");
    const tokens = await formPost(base, "/token", {
      grant_type: "authorization_code", client_id: clientId, code,
      redirect_uri: redirectUri, code_verifier: verifier,
    });
    if (tokens.scope !== "userio") throw new Error("UserIO выдал неожиданный доступ.");
    const accountLabel = await readback(tokens.access_token, connectionId);
    await saveTokens(tokens, connectionId);
    return { ok: true, accountLabel: accountLabel || "аккаунт UserIO", purpose: "Доступ расширения Universal UserIO Agent к UserIO" };
  }

  async function status(connectionId) {
    const [a,,,,,endpointKey]=keys(connectionId), values = await chrome.storage.local.get([a,endpointKey]);
    if (!values[a]) return { ok: true, connected: false };
    if (values[endpointKey] !== await endpoint(connectionId)) {
      await chrome.storage.local.remove(keys(connectionId));
      return { ok: true, connected: false };
    }
    try {
      const token = await accessToken(connectionId);
      const accountLabel = await readback(token, connectionId);
      return { ok: true, connected: true, accountLabel: accountLabel || "аккаунт UserIO" };
    } catch (error) {
      return { ok: false, connected: false, error: error.message };
    }
  }

  async function disconnect(connectionId) {
    await chrome.storage.local.remove(keys(connectionId));
    return { ok: true, connected: false };
  }

  root.UserIOOAuth = { accessToken, connect, disconnect, status };
})(typeof self !== "undefined" ? self : this);
