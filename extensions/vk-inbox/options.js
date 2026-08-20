const endpoint = document.getElementById("endpoint");
const token = document.getElementById("token");
chrome.storage.local.get({endpoint: "https://msg.bezrabotnyi.com/v1/messages", token: ""}).then((data) => { endpoint.value = data.endpoint; token.value = data.token; });
document.getElementById("save").onclick = async () => { await chrome.storage.local.set({endpoint: endpoint.value.trim(), token: token.value}); document.getElementById("status").textContent = " Сохранено"; };
