// Credential handoff only. Native UserIO owns chats, calls and token rotation.
(function (root) {
  const RULE = 90101;
  let pending = false;
  async function backend(connectionId) {
    if (!connectionId) throw new Error("choose_userio_connection");
    const saved = await chrome.storage.local.get({dionOwnerConnection: ""});
    if (saved.dionOwnerConnection && saved.dionOwnerConnection !== connectionId) throw new Error("dion_owned_by_another_connection");
    const settings = await root.UserIO.settings(connectionId);
    const url = new URL(settings.endpoint);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) {
      throw new Error("dion_handoff_requires_https");
    }
    return root.UserIO.call("GET", "/v1/dion/accounts", undefined, connectionId);
  }
  async function blockRefresh() {
    await chrome.declarativeNetRequest.updateDynamicRules({removeRuleIds: [RULE], addRules: [{
      id: RULE, priority: 1, action: {type: "block"}, condition: {
        regexFilter: "^https://[^/]*dion\\.vc/.*(auth/refresh|refresh/web)",
        resourceTypes: ["xmlhttprequest", "other"],
      },
    }]});
  }
  async function importAccount(connectionId) {
    if (pending) throw new Error("dion_handoff_in_progress");
    pending = true;
    try {
      const status = await backend(connectionId);
      await chrome.storage.local.set({dionOwnerConnection: connectionId});
      await blockRefresh();
      const cookies = await chrome.cookies.getAll({domain: "dion.vc"});
      const one = (name) => {
        const values = [...new Set(cookies.filter(c => c.name === name).map(c => c.value))];
        if (values.length !== 1 || !values[0]) throw new Error("dion_login_required_or_ambiguous");
        return values[0];
      };
      const access = one("vc-access-token"), refresh = one("vc-refresh-token");
      let claims;
      try { claims = JSON.parse(atob(access.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); }
      catch (_) { throw new Error("dion_invalid_access_token"); }
      if (!claims.user_id || !claims.org_id) throw new Error("dion_identity_missing");
      const id = `dion:${claims.org_id}:${claims.user_id}`;
      const existing = (status.accounts || []).find(a => a.id === id);
      if (existing && ["connected", "identity_pending", "refresh_pending"].includes(existing.state)) {
        throw new Error("dion_already_owned_by_userio");
      }
      const result = await root.UserIO.call("POST", "/v1/dion/accounts", {
        access_token: access, refresh_token: refresh,
        provider_user_id: claims.user_id, organization_id: claims.org_id,
        expected_generation: existing?.generation || 0,
      }, connectionId);
      const account = result;
      if (!account || account.id !== id || account.state !== "connected" ||
          account.generation !== (existing?.generation || 0) + 1) throw new Error("dion_handoff_not_confirmed");
      // Never persist a second credential chain in extension storage.
      for (const cookie of cookies.filter(c => ["vc-access-token", "vc-refresh-token"].includes(c.name))) {
        await chrome.cookies.remove({url: `https://${cookie.domain.replace(/^\./, "")}${cookie.path}`, name: cookie.name, storeId: cookie.storeId});
      }
      return {ok: true, account};
    } finally { pending = false; }
    // Keep the refresh block after errors: an unknown import may have succeeded.
  }
  async function beginReauth(connectionId) {
    const status = await backend(connectionId);
    if (pending || (status.accounts || []).some(a => ["connected", "identity_pending"].includes(a.state))) {
      throw new Error("dion_already_owned_by_userio");
    }
    await chrome.declarativeNetRequest.updateDynamicRules({removeRuleIds: [RULE]});
    await chrome.tabs.create({url: "https://dion.vc"});
    return {ok: true};
  }
  root.UserIODion = {importAccount, beginReauth};
})(self);
