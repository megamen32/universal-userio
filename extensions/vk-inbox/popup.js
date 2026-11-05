(function () {
  const $ = (id) => document.getElementById(id);
  const send = (kind) => new Promise((resolve) => chrome.runtime.sendMessage({ kind }, (value) => resolve(value || { ok: false, error: chrome.runtime.lastError?.message })));
  const settings = () => chrome.runtime.openOptionsPage();
  $("settings").onclick = settings;
  $("userioLogin").onclick = settings;
  $("chatgptSetup").onclick = settings;
  async function refresh() {
    const user = await send("userioStatus");
    const connected = !!user.connected;
    $("userioBadge").textContent = connected ? "Выполнен вход" : "Нет входа";
    $("userioBadge").className = connected ? "good" : "muted";
    $("userioAccount").textContent = connected ? user.accountLabel : (user.error || "Войдите, чтобы подключить браузер.");
    $("userioLogin").hidden = connected;
    if (!connected) { $("chatgptBadge").textContent = "Не подключён"; return; }
    const result = await send("gptConnectionStatus");
    const accounts = result.ok && Array.isArray(result.accounts) ? result.accounts : [];
    $("chatgptBadge").textContent = accounts.length ? "Подключён" : "Не подключён";
    $("chatgptBadge").className = accounts.length ? "good" : "muted";
    const report = result.gptSyncReport;
    const matches = report && accounts.some(x => x.email && x.email.toLowerCase() === String(report.email || "").toLowerCase());
    const sync = matches ? "Обновление последних чатов: " + new Date(report.completedAt).toLocaleString() : "Синхронизация не подтверждена";
    $("chatgptAccount").textContent = result.ok ? (accounts.length ? accounts.map(x => x.email || x.name || x.slug).join(", ") + " · " + sync : "Выберите аккаунт в этом браузере.") : "Не удалось проверить состояние подключения.";
    $("chatgptSetup").textContent = accounts.length ? "Управлять" : "Подключить";
  }
  $("downloadUpdate").onclick = () => chrome.tabs.create({ url: "https://msg.bezrabotnyi.com/vk-userio-extension-mv3.zip" });
  async function refreshVersion() {
    const result = await send("extensionVersion");
    if (!result.ok) { $("versionStatus").textContent = `Версия ${result.current || chrome.runtime.getManifest().version} · не удалось проверить обновления`; return; }
    $("versionStatus").textContent = result.available ? `Версия ${result.current} · доступна ${result.latest}` : `Версия ${result.current} · обновлений нет`;
    $("downloadUpdate").hidden = !result.available;
  }
  refreshVersion();
  refresh();
})();
