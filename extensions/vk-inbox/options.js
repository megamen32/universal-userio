(function () {
  const $ = (id) => document.getElementById(id);
  const DEFAULTS = { endpoint: "http://127.0.0.1:18093", token: "", routeId: "vk-browser" };
  chrome.storage.local.get(DEFAULTS).then((s) => {
    $("endpoint").value = s.endpoint;
    $("token").value = s.token;
    $("routeId").value = s.routeId || "vk-browser";
  });
  $("save").addEventListener("click", async () => {
    await chrome.storage.local.set({
      endpoint: $("endpoint").value.trim() || DEFAULTS.endpoint,
      token: $("token").value,
      routeId: $("routeId").value.trim() || DEFAULTS.routeId,
    });
    $("status").textContent = "Сохранено";
    setTimeout(() => { $("status").textContent = ""; }, 1500);
  });
})();
