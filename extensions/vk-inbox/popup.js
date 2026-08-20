const status = document.getElementById("status");
document.getElementById("options").onclick = () => chrome.runtime.openOptionsPage();
document.getElementById("capture").onclick = async () => {
  status.textContent = "Читаю открытый чат…";
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !tab.url?.startsWith("https://vk.com/")) { status.textContent = "Откройте чат VK Web."; return; }
  const [{result}] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => window.userioVkCapture?.() || {messages: []}});
  const settings = await chrome.storage.local.get({endpoint: "https://msg.bezrabotnyi.com/v1/messages", token: ""});
  let sent = 0;
  for (const message of (result?.messages || [])) {
    const response = await fetch(settings.endpoint, {method: "POST", headers: {"Content-Type": "application/json", ...(settings.token ? {Authorization: `Bearer ${settings.token}`} : {})}, body: JSON.stringify({route_id: "vk-browser", message: {schema: "universal.inbox.message.v1", source: "vk", message_id: message.id, sender: result.peer, body: message.body}})});
    if (!response.ok) throw new Error(`UserIO ответил ${response.status}`);
    sent += 1;
  }
  status.textContent = sent ? `Отправлено в UserIO: ${sent}` : "Новых сообщений в открытом чате не найдено.";
};
