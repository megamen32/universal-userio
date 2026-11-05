// Public OAuth client for the Chrome extension. No browser credentials or
// site cookies are read here; the user completes UserIO's own consent screen.
(function (root) {
  const TOKEN_KEYS = ["oauthAccessToken", "oauthRefreshToken", "oauthExpiresAt", "oauthClientId", "oauthRedirectUri", "oauthEndpoint"];

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

  async function endpoint() {
    const configured = root.USERIO_CONFIG || {};
    const values = await chrome.storage.local.get({ endpoint: configured.endpoint || "http://127.0.0.1:18093" });
    const value = String(values.endpoint || configured.endpoint || "http://127.0.0.1:18093").replace(/\/+$/, "");
    const parsed = new URL(value);
    if (!new Set(["http:", "https:"]).has(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error("Укажите корректный HTTP(S) адрес UserIO без логина, пароля и параметров.");
    }
    if (parsed.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname)) {
      throw new Error("Для удалённого UserIO требуется HTTPS.");
    }
    return value;
  }

  async function clientFor(base) {
    const redirectUri = chrome.identity.getRedirectURL("userio");
    const existing = await chrome.storage.local.get(["oauthClientId", "oauthRedirectUri", "oauthEndpoint"]);
    if (existing.oauthClientId && existing.oauthRedirectUri === redirectUri && existing.oauthEndpoint === base) {
      return { clientId: existing.oauthClientId, redirectUri };
    }
    const response = await fetch(base + "/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_uris: [redirectUri], token_endpoint_auth_method: "none" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.client_id) throw new Error(data.error || `OAuth client registration failed (HTTP ${response.status})`);
    await chrome.storage.local.set({ oauthClientId: data.client_id, oauthRedirectUri: redirectUri, oauthEndpoint: base });
    return { clientId: data.client_id, redirectUri };
  }

  async function saveTokens(data) {
    if (!data.access_token || !data.refresh_token || data.token_type !== "Bearer") {
      throw new Error("UserIO returned an incomplete OAuth token response");
    }
    await chrome.storage.local.set({
      oauthAccessToken: data.access_token,
      oauthRefreshToken: data.refresh_token,
      oauthExpiresAt: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
    });
  }

  let refreshInFlight = null;
  async function accessToken() {
    const values = await chrome.storage.local.get(TOKEN_KEYS);
    if (!values.oauthAccessToken) return String((await chrome.storage.local.get({ token: "" })).token || "");
    if (values.oauthEndpoint !== await endpoint()) {
      await chrome.storage.local.remove(TOKEN_KEYS);
      return String((await chrome.storage.local.get({ token: "" })).token || "");
    }
    if (Number(values.oauthExpiresAt) > Date.now() + 60_000) return values.oauthAccessToken;
    if (!values.oauthRefreshToken || !values.oauthClientId) throw new Error("Сеанс UserIO истёк: подключите аккаунт снова.");
    if (!refreshInFlight) {
      refreshInFlight = (async () => {
        const data = await formPost(await endpoint(), "/token", {
          grant_type: "refresh_token", client_id: values.oauthClientId, refresh_token: values.oauthRefreshToken,
        });
        await saveTokens(data);
        return data.access_token;
      })().finally(() => { refreshInFlight = null; });
    }
    return refreshInFlight;
  }

  async function readback(token) {
    const base = await endpoint();
    const response = await fetch(base + "/v1/inbox", { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error(`UserIO login completed but account readback failed (HTTP ${response.status})`);
    await response.json().catch(() => ({}));
    const profile = await fetch(base + "/v1/connection-principal", { headers: { Authorization: `Bearer ${token}` } });
    if (!profile.ok) return "";
    const info = await profile.json().catch(() => ({}));
    return String(info.username || "");
  }

  async function connect() {
    const base = await endpoint();
    const { clientId, redirectUri } = await clientFor(base);
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
    const accountLabel = await readback(tokens.access_token);
    await saveTokens(tokens);
    return { ok: true, accountLabel: accountLabel || "аккаунт UserIO", purpose: "Доступ расширения Universal UserIO Agent к UserIO" };
  }

  async function status() {
    const values = await chrome.storage.local.get(TOKEN_KEYS);
    if (!values.oauthAccessToken) return { ok: true, connected: false };
    if (values.oauthEndpoint !== await endpoint()) {
      await chrome.storage.local.remove(TOKEN_KEYS);
      return { ok: true, connected: false };
    }
    try {
      const token = await accessToken();
      const accountLabel = await readback(token);
      return { ok: true, connected: true, accountLabel: accountLabel || "аккаунт UserIO" };
    } catch (error) {
      return { ok: false, connected: false, error: error.message };
    }
  }

  async function disconnect() {
    await chrome.storage.local.remove(TOKEN_KEYS);
    return { ok: true, connected: false };
  }

  root.UserIOOAuth = { accessToken, connect, disconnect, status };
})(typeof self !== "undefined" ? self : this);
