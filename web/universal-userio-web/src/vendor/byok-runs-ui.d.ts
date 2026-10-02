// Types for the vendored byok runs UI (@bezrabotnyi/byok dist/runs-ui.js).
export type ByokLedgerRecord = { id: string; ts: string; userId: string; taskId: string; sessionId: string; model: string; providerHost: string; inputTokens: number | null; outputTokens: number | null; cacheReadTokens: number | null; cacheWriteTokens: number | null; totalTokens: number | null; costUsd: number | null; costRub: number | null; fxRate: number | null; durationMs: number; ok: boolean; error?: string; promptPreview?: string; completionPreview?: string; system?: string; prompt?: string; reasoning?: string; completion?: string }
export type ByokLedgerTotals = { calls: number; inputTokens: number; outputTokens: number; costUsd: number; costUsdKnown: boolean; costRub: number; fxRate: number | null }
export declare function renderRunsTable(runs: ByokLedgerRecord[], totals?: ByokLedgerTotals, options?: { emptyHint?: string; selectedId?: string }): string
export declare const BYOK_RUNS_CSS: string
export declare function defineByokRunsView(customElements?: CustomElementRegistry): void
export declare function renderRunDetails(run: ByokLedgerRecord | null): string
export declare const BYOK_RUN_DETAILS_CSS: string
export declare function defineByokRunDetails(customElements?: CustomElementRegistry): void
