// Vendored from @bezrabotnyi/byok v1.8.3 dist/runs-ui.js — refresh with:
// cp ~/agents-projects/byok/dist/runs-ui.js web/universal-userio-web/src/vendor/byok-runs-ui.js
// src/runs-ui.ts
function esc(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}
var money = (usd) => usd == null ? "—" : `$${usd < 0.01 && usd > 0 ? usd.toFixed(4) : usd.toFixed(2)}`;
var tokens = (value) => value == null ? "—" : value.toLocaleString("ru-RU");
function renderRunsTable(runs, totals, options = {}) {
  if (!runs.length)
    return `<p class="byok-empty">${esc(options.emptyHint ?? "Прогонов пока нет")}</p>`;
  const head = totals ? `<div class="byok-runs__totals">
        <span>${totals.calls} прогонов</span>
        <span>${tokens(totals.inputTokens)} in / ${tokens(totals.outputTokens)} out</span>
        <span>${money(totals.costUsd)} · ${rub(totals.costRub ?? null)}${totals.costUsdKnown ? "" : " (часть без цен)"}</span>
      </div>` : "";
  const selectedId = options.selectedId;
  const rows = runs.map((run) => `
    <tr class="byok-runs__row${run.ok ? "" : " byok-runs__row--error"}${selectedId === run.id ? " byok-runs__row--selected" : ""}" data-byok-run-id="${esc(run.id)}" title="${esc(run.error || run.promptPreview || "")}">
      <td class="byok-runs__ts">${esc(run.ts.slice(5, 16).replace("T", " "))}</td>
      <td class="byok-runs__model">${esc(run.model)}</td>
      <td class="byok-runs__task">${esc(run.taskId || run.userId || "—")}</td>
      <td class="byok-runs__num">${tokens(run.inputTokens)}</td>
      <td class="byok-runs__num">${tokens(run.outputTokens)}</td>
      <td class="byok-runs__num">${money(run.costUsd)}<span class="byok-runs__rub"> ${rub(run.costRub)}</span></td>
      <td class="byok-runs__num">${(run.durationMs / 1000).toFixed(1)}s</td>
    </tr>`).join("");
  return `
    <div class="byok-runs">
      ${head}
      <table class="byok-runs__table">
        <thead><tr><th>когда</th><th>модель</th><th>задача</th><th>in</th><th>out</th><th>$</th><th>t</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
var BYOK_RUNS_CSS = `
.byok-runs__rub { opacity: .7; }
.byok-runs { font: inherit; color: inherit; }
.byok-runs__totals { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; opacity: .85; padding: 6px 0; }
.byok-runs__table { width: 100%; border-collapse: collapse; font-size: 12px; }
.byok-runs__table th { text-align: left; font-weight: 600; opacity: .6; padding: 4px 8px 4px 0; border-bottom: 1px solid var(--byok-border, #2b3554); }
.byok-runs__table td { padding: 4px 8px 4px 0; border-bottom: 1px solid color-mix(in srgb, var(--byok-border, #2b3554) 50%, transparent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
.byok-runs__row--error td { color: var(--byok-error, #ff6b6b); }
.byok-runs__row { cursor: pointer; }
.byok-runs__row:hover td { background: color-mix(in srgb, var(--byok-accent, #6d7cff) 8%, transparent); }
.byok-runs__row--selected td { background: color-mix(in srgb, var(--byok-accent, #6d7cff) 14%, transparent); }
.byok-runs__ts, .byok-runs__num { opacity: .75; font-variant-numeric: tabular-nums; }
.byok-empty { font-size: 12px; opacity: .6; }
`;
function defineByokRunsView(customElements) {
  const target = customElements ?? (typeof globalThis !== "undefined" ? globalThis.customElements : undefined);
  if (!target || target.get("byok-runs-view"))
    return;

  class ByokRunsView extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {
  }) {
    selectedId = "";
    static get observedAttributes() {
      return ["runs", "totals"];
    }
    get runs() {
      return this.getAttribute("runs") || "[]";
    }
    get totals() {
      return this.getAttribute("totals") || "";
    }
    connectedCallback() {
      this.render();
      this.addEventListener("click", this.onClick);
    }
    disconnectedCallback() {
      this.removeEventListener("click", this.onClick);
    }
    attributeChangedCallback() {
      if (this.isConnected)
        this.render();
    }
    onClick = (event) => {
      const row = event.target?.closest?.("[data-byok-run-id]");
      if (!row)
        return;
      const runId = row.getAttribute("data-byok-run-id") || "";
      const record = this.currentRuns().find((item) => item.id === runId);
      if (!record)
        return;
      this.selectedId = runId;
      this.render();
      this.dispatchEvent(new CustomEvent("byok-run-open", { detail: record, bubbles: true, composed: true }));
    };
    currentRuns() {
      try {
        const value = JSON.parse(this.runs);
        return Array.isArray(value) ? value : [];
      } catch {
        return [];
      }
    }
    render() {
      let parsedTotals;
      try {
        const value = JSON.parse(this.totals);
        if (value && typeof value === "object")
          parsedTotals = value;
      } catch {}
      this.innerHTML = `<style>${BYOK_RUNS_CSS}</style>` + renderRunsTable(this.currentRuns(), parsedTotals, { selectedId: this.selectedId });
    }
  }
  target.define("byok-runs-view", ByokRunsView);
}
var rub = (value) => value == null ? "—" : `${value < 0.01 && value > 0 ? value.toFixed(4) : value.toFixed(2)} ₽`;
var usd = (value) => value == null ? "—" : `$${value < 0.01 && value > 0 ? value.toFixed(4) : value.toFixed(2)}`;
function section(title, body, options = {}) {
  if (!body || !body.trim())
    return "";
  return `<details class="byok-detail__section"${options.collapsed ? "" : " open"}>
    <summary>${esc(title)}</summary>
    <pre class="byok-detail__pre${options.muted ? " byok-detail__pre--muted" : ""}">${esc(body)}</pre>
  </details>`;
}
function renderRunDetails(run) {
  if (!run)
    return '<p class="byok-empty">Прогон не выбран</p>';
  const meta = [
    ["модель", run.model],
    ["задача", run.taskId || "—"],
    ["пользователь", run.userId || "—"],
    ["сессия", run.sessionId || "—"],
    ["токены", `${tokens(run.inputTokens)} in / ${tokens(run.outputTokens)} out`],
    ["стоимость", `${usd(run.costUsd)} · ${rub(run.costRub)}${run.fxRate ? ` (курс ${run.fxRate})` : ""}`],
    ["длительность", `${(run.durationMs / 1000).toFixed(1)}s`]
  ];
  return `
  <div class="byok-detail" data-byok-run-id="${esc(run.id)}">
    <div class="byok-detail__meta">${meta.map(([key, value]) => `<span class="byok-detail__kv"><b>${esc(key)}</b>${esc(value)}</span>`).join("")}</div>
    ${run.ok ? "" : `<p class="byok-detail__error">Ошибка: ${esc(run.error || "неизвестная")}</p>`}
    ${section("Системный промпт", run.system, { muted: true })}
    ${section("Ввод", run.prompt)}
    ${section("Размышления", run.reasoning, { collapsed: true, muted: true })}
    ${section("Ответ", run.completion ?? run.completionPreview)}
  </div>`;
}
var BYOK_RUN_DETAILS_CSS = `
.byok-detail { font: inherit; color: inherit; }
.byok-detail__meta { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12px; padding: 6px 0; }
.byok-detail__kv { display: flex; gap: 6px; }
.byok-detail__kv b { opacity: .6; font-weight: 600; }
.byok-detail__error { color: var(--byok-error, #ff6b6b); font-size: 12px; }
.byok-detail__section { border-top: 1px solid color-mix(in srgb, var(--byok-border, #2b3554) 60%, transparent); }
.byok-detail__section summary { cursor: pointer; font-size: 12px; opacity: .8; padding: 6px 0; }
.byok-detail__pre { margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; background: var(--byok-pre-bg, rgba(127,127,127,.08)); font: 12px/1.5 ui-monospace, monospace; white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow: auto; }
.byok-detail__pre--muted { opacity: .75; }
`;
function defineByokRunDetails(customElements) {
  const target = customElements ?? (typeof globalThis !== "undefined" ? globalThis.customElements : undefined);
  if (!target || target.get("byok-run-details"))
    return;

  class ByokRunDetails extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {
  }) {
    static get observedAttributes() {
      return ["run"];
    }
    get run() {
      return this.getAttribute("run") || "";
    }
    connectedCallback() {
      this.render();
    }
    attributeChangedCallback() {
      if (this.isConnected)
        this.render();
    }
    render() {
      let parsed = null;
      try {
        const value = JSON.parse(this.run);
        if (value && typeof value === "object")
          parsed = value;
      } catch {}
      this.innerHTML = `<style>${BYOK_RUNS_CSS}${BYOK_RUN_DETAILS_CSS}</style>` + renderRunDetails(parsed);
    }
  }
  target.define("byok-run-details", ByokRunDetails);
}
export {
  renderRunsTable,
  renderRunDetails,
  defineByokRunsView,
  defineByokRunDetails,
  BYOK_RUN_DETAILS_CSS,
  BYOK_RUNS_CSS
};
