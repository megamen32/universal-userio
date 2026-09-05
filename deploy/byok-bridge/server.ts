// Thin local bridge over @bezrabotnyi/byok: one endpoint, loopback-only.
// UserIO stores each user's BYOK settings and calls POST /chat here; the
// package enforces HTTPS-only, public-DNS and private-IP blocking for us.
import { runByokModelTracked } from '@bezrabotnyi/byok/ledger'
import { ByokLedger } from '@bezrabotnyi/byok/ledger'
import { buildByokPresetsFromModelsDev, fetchByokCatalogResponse, previewByokPresets, type ByokPreset } from '@bezrabotnyi/byok/catalog'
import { buildShowcasePresets } from '@bezrabotnyi/byok/showcase'

// Cost ledger: every /chat is priced and recorded with the caller's
// user/task context; BYOK_LEDGER_PERSIST=0 disables remembering.
const ledger = new ByokLedger({
  persist: true,
  file: process.env.BYOK_LEDGER_FILE || '/var/lib/universal-userio/byok-ledger.jsonl',
  fxFile: process.env.BYOK_FX_FILE || '/var/lib/universal-userio/byok-fx.json',
})

const port = Number(process.env.PORT || 30110)
const token = process.env.BYOK_BRIDGE_TOKEN || ''

type ChatRequest = {
  base_url?: string
  api_key?: string
  model_id?: string
  system?: string
  prompt?: string
  max_output_tokens?: number
  reasoning_effort?: 'default' | 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
}

const json = (body: unknown, status = 200) => Response.json(body, { status })

// anthropic-style base URLs get the anthropic wire format, others OpenAI chat
function apiFormatFor(baseUrl: string): 'anthropic' | 'chat-completions' {
  return /\/anthropic(\/|$)/.test(baseUrl) ? 'anthropic' : 'chat-completions'
}

Bun.serve({
  port, hostname: '127.0.0.1',
  async fetch(request) {
    const url = new URL(request.url)
    if (url.pathname === '/health') return json({ ok: true })
    if (url.pathname === '/runs') {
      const filter = {
        userId: url.searchParams.get('user_id') || undefined,
        taskId: url.searchParams.get('task_id') || undefined,
        sessionId: url.searchParams.get('session_id') || undefined,
        limit: Number(url.searchParams.get('limit')) > 0 ? Number(url.searchParams.get('limit')) : 100,
      }
      return json({ runs: ledger.entries(filter), totals: ledger.totals(filter) })
    }
    if (url.pathname === '/presets') {
      // Default: the compareai model showcase (chart top, live prices from
      // models.dev when reachable). ?legacy=1 returns the provider-level
      // fallback list instead.
      if (url.searchParams.get('legacy') === '1') {
        const catalog = await fetchByokCatalogResponse()
        return json({ source: catalog.source, fetchedAt: catalog.fetchedAt, presets: previewByokPresets(catalog.presets) })
      }
      const catalog = await fetchByokCatalogResponse().catch(() => null)
      let showcase: ByokPreset[] = buildShowcasePresets()
      if (catalog && catalog.source === 'models.dev') {
        const byModel = new Map(catalog.presets.map((preset) => [preset.modelId, preset]))
        showcase = showcase.map((preset) => ({ ...preset, ...(byModel.get(preset.modelId) ?? {}) , id: preset.id, label: preset.label, modelId: preset.modelId }))
      }
      return json({
        source: 'compareai-showcase',
        fetchedAt: new Date().toISOString(),
        presets: previewByokPresets(showcase),
      })
    }
    if (url.pathname !== '/chat') return json({ error: 'not_found' }, 404)
    if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)
    if (!token || request.headers.get('authorization') !== `Bearer ${token}`) {
      return json({ error: 'unauthorized' }, 401)
    }
    const body = (await request.json().catch(() => null)) as ChatRequest | null
    const baseUrl = String(body?.base_url || '').trim()
    const apiKey = String(body?.api_key || '').trim()
    const modelId = String(body?.model_id || '').trim()
    const prompt = String(body?.prompt || '')
    if (!baseUrl || !apiKey || !modelId || !prompt) {
      return json({ error: 'base_url, api_key, model_id and prompt are required' }, 400)
    }
    try {
      const result = await runByokModelTracked({
        apiFormat: apiFormatFor(baseUrl),
        baseUrl,
        apiKey,
        modelId,
        reasoningEffort: body?.reasoning_effort || 'default',
        maxOutputTokens: Number(body?.max_output_tokens) > 0 ? Number(body?.max_output_tokens) : 2000,
      }, {
        system: String(body?.system || 'Do not claim to send messages. Produce drafts only.'),
        prompt,
      }, {
        userId: String(body?.user_id || ''),
        taskId: String(body?.task_id || ''),
        sessionId: String(body?.session_id || ''),
        ledger,
      })
      return json({ text: result.text, usage: result.usage, costUsd: result.costUsd })
    } catch (error) {
      return json({ error: error instanceof Error ? error.message : 'byok call failed' }, 502)
    }
  },
})
console.log(`byok-bridge listening on 127.0.0.1:${port}`)
