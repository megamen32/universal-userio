(function () {
  const $ = (id) => document.getElementById(id);
  const CFG = window.USERIO_CONFIG || {};
  const DEFAULTS = { endpoint: CFG.endpoint || "http://127.0.0.1:18093" };
  chrome.storage.local.get(DEFAULTS).then((s) => {
    $("endpoint").value = s.endpoint;
    refreshUserIO();
    refreshChatGPT();
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

  async function refreshChatGPT() {
    const result = await message("gptConnectionStatus");
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
        const response = await message("gptDisconnect", { slug: account.slug });
        if (!response || !response.ok) { $("chatgptStatus").textContent = `Не удалось отключить: ${response?.error || "нет ответа"}`; disconnect.disabled = false; return; }
        await refreshChatGPT();
      });
      row.append(name, disconnect); list.append(row);
    }
  }

  let selectedChatGPT = null;
  $("chatgptSelect").addEventListener("click", async () => {
    const result = await message("gptIdentity");
    selectedChatGPT = result && result.ok ? result.fingerprint : null;
    $("chatgptStatus").textContent = selectedChatGPT
      ? `Выбран аккаунт этого профиля: ${result.name || result.email || "имя не определено"}${result.email && result.name ? ` (${result.email})` : ""}`
      : `Аккаунт не найден: ${(result && result.error) || "войдите в ChatGPT в этом профиле Chrome"}`;
    $("chatgptConnect").hidden = !selectedChatGPT;
  });

  $("chatgptConnect").addEventListener("click", async () => {
    if (!selectedChatGPT || !window.confirm("Передать cookies выбранного аккаунта ChatGPT в UserIO для подключения?")) return;
    $("chatgptConnect").disabled = true;
    try {
      const result = await message("gptRegister", { userInitiated: true, expectedFingerprint: selectedChatGPT });
      if (!result || !result.ok) throw new Error(result && result.error || "Передача не удалась");
      selectedChatGPT = null;
      await refreshChatGPT();
    } catch (error) { $("chatgptStatus").textContent = "Ошибка подключения ChatGPT: " + error.message; }
    finally { $("chatgptConnect").disabled = false; }
  });
})();
