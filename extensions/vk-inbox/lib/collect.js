// Universal collect agent: polls UserIO for site data tasks and executes
// them with the user's real browser session. Tasks come only from the
// configured UserIO endpoint; results go only back to that endpoint.
// Recipes of kind "fetch" run cross-origin with cookies — this is the whole
// point: sites see the logged-in user, UserIO never sees site credentials.

(function (root) {
  const lib = {};
  const POLL_ALARM = "userio-collect-poll";
  const DEFAULT_EVERY_SEC = 300;

  lib.start = () => {
    chrome.alarms.create(POLL_ALARM, { periodInMinutes: 1 });
    chrome.alarms.onAlarm.addListener((a) => {
      if (a.name === POLL_ALARM) lib.runDue().catch(() => {});
    });
  };

  lib.state = async () =>
    (await chrome.storage.local.get({ collectState: {} })).collectState;

  lib.runDue = async () => {
    const { tasks } = await self.UserIO.call("GET", "/v1/collect/tasks");
    const state = await lib.state();
    state.lastRun = state.lastRun || {};
    let ran = 0;
    for (const task of tasks) {
      const every = Math.max(30, Number(task.every_sec) || DEFAULT_EVERY_SEC) * 1000;
      if (Date.now() - (state.lastRun[task.id] || 0) < every) continue;
      state.lastRun[task.id] = Date.now();
      ran += 1;
      const outcome = await lib.runTask(task).catch(
        (e) => ({ status: "error", error: String(e && e.message || e) })
      );
      state.last = { at: Date.now(), task_id: task.id, status: outcome.status };
    }
    await chrome.storage.local.set({ collectState: state });
    return { ok: true, ran, total: tasks.length };
  };

  async function postResult(result) {
    return self.UserIO.call("POST", "/v1/collect/results", {
      schema: "universal.collect.result.v1",
      ...result,
      fetched_at: new Date().toISOString(),
      agent: "universal-agent/" + chrome.runtime.getManifest().version,
    });
  }

  lib.runTask = async (task) => {
    const recipe = task.recipe || {};
    try {
      if (recipe.kind !== "fetch" || !recipe.url) throw new Error("unsupported recipe");
      const response = await fetch(recipe.url, {
        method: recipe.method || "GET",
        headers: recipe.headers || undefined,
        body: recipe.body != null ? String(recipe.body) : undefined,
        credentials: recipe.credentials === "omit" ? "omit" : "include",
      });
      const text = await response.text();
      let data = text;
      if ((recipe.response || "json") === "json") {
        try { data = JSON.parse(text); } catch (_) { /* keep raw text */ }
      }
      const status = response.ok ? "ok" : "error";
      await postResult({ task_id: task.id, status, http_status: response.status, data }).catch(() => {});
      return { status };
    } catch (error) {
      const message = String(error && error.message || error);
      await postResult({ task_id: task.id, status: "error", http_status: 0, error: message }).catch(() => {});
      return { status: "error", error: message };
    }
  };

  root.Collect = lib;
})(typeof self !== "undefined" ? self : this);
