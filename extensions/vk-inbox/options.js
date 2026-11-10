(function () {
  const $ = (id) => document.getElementById(id);
  const CFG = window.USERIO_CONFIG || {};
  const DEFAULTS = { endpoint: CFG.endpoint || "http://127.0.0.1:18093" };
  chrome.storage.local.get(DEFAULTS).then((s) => {
    $("endpoint").value = s.endpoint;
    refreshUserIO();
    refreshChatGPT();
    refreshConnections();
  });
  $("save").addEventListener("click", async () => {
    await chrome.storage.local.set({
      endpoint: $("endpoint").value.trim() || DEFAULTS.endpoint,
    });
    $("status").textContent = "Сохранено";
    setTimeout(() => { $("status").textContent = ""; }, 1500);
  });

  function message(kind, extra = {}) {
    return new Promise((resolve) => chrome.runtime.sendMessage({ kind, ...extra }, resolve));
  }

  async function refreshConnections() {
    if (!$("connections") || !$("connectionAccounts") || !$("connectionsStatus")) return;
    const [cr, ar, br] = await Promise.all([message("connectionsList"), message("accountsList"), message("accountBindingsGet")]);
    if ($("legacyUserIOCard")) $("legacyUserIOCard").hidden = !!cr?.ok;
    if ($("legacyChatGPTCard")) $("legacyChatGPTCard").hidden = !!cr?.ok;
    const connections = cr?.ok && Array.isArray(cr.connections) ? cr.connections : [];
    const accounts = ar?.ok && Array.isArray(ar.accounts) ? ar.accounts.map((account) => ({ ...account, accountKey: account.accountKey || account.key })) : [];
    const bindings = br?.ok && br.bindings ? br.bindings : {};
    $("connections").replaceChildren(); $("connectionAccounts").replaceChildren();
    $("connectionsStatus").textContent = connections.length ? "Статус показывается отдельно для каждого подключения." : "Подключения ещё не настроены.";
    for (const connection of connections) { const row = document.createElement("div"); row.className = "account connection-row"; row.dataset.connectionId = connection.id; const name = document.createElement("span"); name.className = "connection-name"; name.textContent = connection.id === "legacy-default" ? "Основное подключение (ранее настроенное)" : (connection.label || "Без названия"); const endpoint = document.createElement("span"); endpoint.className = "connection-endpoint"; endpoint.textContent = connection.endpoint || ""; const info = document.createElement("div"); info.className = "connection-info"; const status = document.createElement("span"); status.className = "connection-status"; status.textContent = connection.lastError ? "Ошибка: " + connection.lastError : (connection.enabled ? "Включено · вход не проверен" : "Отключено"); const check = document.createElement("button"); check.textContent = "Проверить"; check.addEventListener("click", async () => { status.textContent = "Проверяем…"; const response = await message("userioStatus", { connectionId: connection.id }); status.textContent = response?.ok ? (response.connected ? "Подключено: " + (response.accountLabel || "аккаунт") : "Нет входа") : "Ошибка: " + (response?.error || "нет ответа"); }); const connect = document.createElement("button"); connect.textContent = "Войти"; connect.addEventListener("click", async () => { status.textContent = "Открываем вход…"; const response = await message("userioConnect", { connectionId: connection.id }); status.textContent = response?.ok ? "Подключено: " + (response.accountLabel || "аккаунт") : "Ошибка входа: " + (response?.error || "нет ответа"); }); const disconnect = document.createElement("button"); disconnect.textContent = "Отключить"; disconnect.addEventListener("click", async () => { const response = await message("userioDisconnect", { connectionId: connection.id }); status.textContent = response?.ok ? "Отключено" : "Ошибка отключения: " + (response?.error || "нет ответа"); }); const chatgpt = document.createElement("button"); chatgpt.textContent = "Подключить ChatGPT"; chatgpt.addEventListener("click", async () => { const identity = await message("gptIdentity"); if (!identity?.ok) { status.textContent = "ChatGPT: " + (identity?.error || "аккаунт не найден"); return; } if (!window.confirm("Подключить аккаунт ChatGPT " + (identity.email || identity.name || "из этого профиля") + " к «" + (connection.label || "подключению") + "»?")) return; const registered = await message("gptRegister", { userInitiated: true, expectedFingerprint: identity.fingerprint, connectionId: connection.id }); status.textContent = registered?.ok ? "ChatGPT подключён" : "Ошибка ChatGPT: " + (registered?.error || "нет ответа"); await refreshChatGPT(connection.id); }); if (connection.enabled && !connection.lastError) void message("userioStatus", { connectionId: connection.id }).then(response => { status.textContent = response?.ok ? (response.connected ? "Подключено: " + (response.accountLabel || "аккаунт") : "Нет входа") : "Ошибка: " + (response?.error || "нет ответа"); }); const manage = document.createElement("button"); manage.className = "secondary"; manage.textContent = "Аккаунты ChatGPT"; manage.addEventListener("click", async () => { $("legacyChatGPTCard").hidden = false; $("chatgptCardTitle").textContent = "ChatGPT · " + name.textContent; $("chatgptSelect").dataset.connectionId = connection.id; await refreshChatGPT(connection.id); }); info.append(name, endpoint, status); const actions = document.createElement("div"); actions.className = "connection-actions"; actions.append(check, connect, disconnect, chatgpt, manage); row.append(info, actions); $("connections").append(row); }
    for (const account of accounts) { const row = document.createElement("div"); row.className = "account binding-row"; row.dataset.accountKey = account.accountKey; const title = document.createElement("strong"); title.className = "binding-title"; title.textContent = (account.label || account.accountId || account.userId || "Аккаунт") + " · " + account.provider; if (account.accountScope) title.title = account.accountScope; row.append(title); for (const connection of connections) { const label = document.createElement("label"); label.className = "binding-choice"; label.dataset.connectionId = connection.id; const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.connectionId = connection.id; checkbox.checked = (bindings[account.accountKey] || []).includes(connection.id); checkbox.addEventListener("change", async () => { const ids = [...row.children].filter((item) => item.dataset?.connectionId && item.children?.[0]?.checked).map((item) => item.dataset.connectionId); await message("accountBindingsSave", { accountKey: account.accountKey, connectionIds: ids }); }); label.append(checkbox, connection.label || connection.id); row.append(label); } $("connectionAccounts").append(row); }
  }

  $("connectionForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("connectionAdd"); button.disabled = true;
    const result = await message("connectionsSave", { input: { label: $("connectionLabel").value.trim(), endpoint: $("connectionEndpoint").value.trim(), enabled: true, kind: "userio" } });
    if (!result?.ok) $("connectionsStatus").textContent = "Не удалось добавить: " + (result?.error || "нет ответа");
    else { $("connectionForm").reset(); $("connectionsStatus").textContent = "Подключение добавлено."; await refreshConnections(); }
    button.disabled = false;
  });

  async function refreshUserIO() {
    const result = await message("userioStatus");
    $("userioStatus").textContent = result && result.connected
      ? result.accountLabel
      : result && result.error ? `Проверка не прошла: ${result.error}` : "Не подключено";
    $("userioDisconnect").hidden = !(result && result.connected);
    $("userioConnect").hidden = !!(result && result.connected);
  }

  $("userioConnect").addEventListener("click", async () => {
    $("userioConnect").disabled = true;
    $("userioStatus").textContent = "В окне UserIO войдите и подтвердите нужный аккаунт.";
    try {
      const result = await message("userioConnect");
      if (!result || !result.ok) throw new Error(result && result.error || "Не удалось подключиться");
      $("userioStatus").textContent = "Подключено: " + result.accountLabel;
      $("userioDisconnect").hidden = false;
      $("userioConnect").hidden = true;
      await refreshChatGPT();
    } catch (error) {
      $("userioStatus").textContent = "Ошибка входа: " + error.message;
    } finally { $("userioConnect").disabled = false; }
  });

  $("userioDisconnect").addEventListener("click", async () => {
    await message("userioDisconnect");
    $("userioStatus").textContent = "Отключено в этом профиле Chrome";
    $("userioDisconnect").hidden = true;
    $("userioConnect").hidden = false;
    await refreshChatGPT();
  });

  async function refreshChatGPT(connectionId) {
    const result = await message("gptConnectionStatus", connectionId ? { connectionId } : {});
    const list = $("chatgptAccounts");
    list.replaceChildren();
    $("chatgptConnect").hidden = true;
    if (!result || !result.ok || !Array.isArray(result.accounts)) {
      $("chatgptStatus").textContent = "Не удалось проверить подключение. Войдите в UserIO и повторите.";
      $("chatgptSelect").hidden = true;
      return;
    }
    const accounts = result.accounts;
    $("chatgptStatus").textContent = accounts.length ? "Уже подключены к UserIO" : "Пока не подключён";
    // The server list includes other browsers; it says nothing about this profile.
    $("chatgptSelect").hidden = false;
    for (const account of accounts) {
      const row = document.createElement("div"); row.className = "account";
      const name = document.createElement("span"); name.textContent = account.email || account.name || account.slug;
      const disconnect = document.createElement("button"); disconnect.className = "danger"; disconnect.textContent = "Отключить";
      disconnect.addEventListener("click", async () => {
        if (!window.confirm(`Отключить ${name.textContent} от UserIO?`)) return;
        disconnect.disabled = true;
        const response = await message("gptDisconnect", { slug: account.slug, ...(connectionId ? { connectionId } : {}) });
        if (!response || !response.ok) { $("chatgptStatus").textContent = `Не удалось отключить: ${response?.error || "нет ответа"}`; disconnect.disabled = false; return; }
        await refreshChatGPT(connectionId);
      });
      row.append(name, disconnect); list.append(row);
    }
  }

  let selectedChatGPT = null;
  $("chatgptSelect").addEventListener("click", async (event) => {
    const connectionId = event?.currentTarget?.dataset?.connectionId || undefined;
    if (!connectionId) { $("chatgptStatus").textContent = "Сначала выберите подключение UserIO."; return; }
    const result = await message("gptIdentity");
    selectedChatGPT = result && result.ok ? result.fingerprint : null;
    $("chatgptConnect").dataset ||= {};
    $("chatgptConnect").dataset.connectionId = connectionId;
    $("chatgptStatus").textContent = selectedChatGPT
      ? `Выбран аккаунт этого профиля: ${result.name || result.email || "имя не определено"}${result.email && result.name ? ` (${result.email})` : ""}`
      : `Аккаунт не найден: ${(result && result.error) || "войдите в ChatGPT в этом профиле Chrome"}`;
    $("chatgptConnect").hidden = !selectedChatGPT;
  });

  $("chatgptConnect").addEventListener("click", async () => {
    if (!selectedChatGPT || !window.confirm("Передать cookies выбранного аккаунта ChatGPT в UserIO для подключения?")) return;
    $("chatgptConnect").disabled = true;
    try {
      const connectionId = $("chatgptConnect").dataset.connectionId;
      if (!connectionId) { $("chatgptStatus").textContent = "Выберите подключение UserIO для этого аккаунта."; return; }
      const result = await message("gptRegister", { userInitiated: true, expectedFingerprint: selectedChatGPT, connectionId });
      if (!result || !result.ok) throw new Error(result && result.error || "Передача не удалась");
      selectedChatGPT = null;
      await refreshChatGPT(connectionId);
    } catch (error) { $("chatgptStatus").textContent = "Ошибка подключения ChatGPT: " + error.message; }
    finally { $("chatgptConnect").disabled = false; }
  });
})();
