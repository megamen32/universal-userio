import assert from "node:assert/strict"
import test from "node:test"

import {
  beginContextSettingsLoad,
  completeContextSettingsLoad,
  contextSettingsPayload,
  contextSettingsValidationError,
  createContextSettingsSession,
  failContextSettingsLoad,
  normalizeContextSettings,
} from "../src/context-settings.ts"

test("legacy responses receive safe cache and adaptive-window defaults", () => {
  const settings = normalizeContextSettings({ message_count: 7, token_budget: 2400 })

  assert.equal(settings.message_count, 7)
  assert.equal(settings.token_budget, 2400)
  assert.equal(settings.max_message_count, 150)
  assert.equal(settings.max_token_budget, 48000)
  assert.equal(settings.cache_channels.telegram, true)
  assert.equal(settings.cache_channels.gmail, false)
  assert.equal(settings.cache_channels.email, false)
  assert.equal(settings.cache_conversation_kinds.direct, true)
})

test("extended responses preserve backend fields and models", () => {
  const settings = normalizeContextSettings({
    message_count: 5,
    max_message_count: 80,
    token_budget: 1600,
    max_token_budget: 12000,
    summary_token_budget: 2000,
    summary_retention_days: 120,
    cache_channels: { telegram: false, gmail: true, matrix: true },
    cache_conversation_kinds: { direct: true, group: true },
    models: { text: "MiniMax-M2.7", image: "MiniMax-M3" },
    future_policy: "keep-me",
  })

  assert.deepEqual(settings.models, { text: "MiniMax-M2.7", image: "MiniMax-M3" })
  assert.equal(settings.cache_channels.matrix, true)
  assert.equal(contextSettingsPayload(settings).future_policy, "keep-me")
})

test("invalid or partial fields do not poison the form", () => {
  const settings = normalizeContextSettings({
    message_count: "not-a-number",
    max_token_budget: null,
    cache_channels: { telegram: "yes", gmail: true },
    cache_conversation_kinds: null,
  })

  assert.equal(settings.message_count, 3)
  assert.equal(settings.max_token_budget, 48000)
  assert.equal(settings.cache_channels.telegram, true)
  assert.equal(settings.cache_channels.gmail, true)
  assert.equal(settings.cache_conversation_kinds.direct, true)
})

test("email is accepted as a rolling-deploy alias for gmail", () => {
  const settings = normalizeContextSettings({ cache_channels: { email: true } })

  assert.equal(settings.cache_channels.email, true)
  assert.equal(settings.cache_channels.gmail, true)
})

test("adaptive ceilings cannot be below the initial context", () => {
  const settings = normalizeContextSettings({
    message_count: 10,
    max_message_count: 5,
    token_budget: 2000,
    max_token_budget: 1000,
  })

  assert.equal(contextSettingsValidationError(settings), "Максимум сообщений не может быть меньше стартового окна")
})

test("adaptive token ceiling respects the model-call minimum", () => {
  const settings = normalizeContextSettings({ token_budget: 0, max_token_budget: 63 })

  assert.equal(contextSettingsValidationError(settings), "Максимум токенов: введите целое число от 64 до 100000")
})

test("failed load retains the confirmed server snapshot and blocks save", () => {
  let session = createContextSettingsSession()
  session = beginContextSettingsLoad(session)
  session = completeContextSettingsLoad(session, { message_count: 9, token_budget: 2600 })
  session = { ...session, draft: { ...session.draft, message_count: 2 } }

  session = beginContextSettingsLoad(session)
  assert.equal(session.draft.message_count, 9)
  assert.equal(session.status, "loading")

  session = failContextSettingsLoad(session, "сервер недоступен")
  assert.equal(session.confirmed.message_count, 9)
  assert.equal(session.draft.message_count, 9)
  assert.equal(session.status, "error")
  assert.match(session.loadError, /Не удалось загрузить сохранённые настройки/)
})

test("each dialog load must succeed before save is allowed", () => {
  let session = createContextSettingsSession()
  session = beginContextSettingsLoad(session)
  session = completeContextSettingsLoad(session, { message_count: 6 })
  assert.equal(session.status, "ready")

  session = beginContextSettingsLoad(session)
  assert.equal(session.status, "loading")
  session = failContextSettingsLoad(session, "тайм-аут")
  assert.equal(session.status, "error")

  session = beginContextSettingsLoad(session)
  session = completeContextSettingsLoad(session, { message_count: 7 })
  assert.equal(session.status, "ready")
  assert.equal(session.confirmed.message_count, 7)
})

test("first failed load cannot confirm defaults", () => {
  const session = failContextSettingsLoad(beginContextSettingsLoad(createContextSettingsSession()), "HTTP 503")
  assert.equal(session.confirmed, null)
  assert.equal(session.status, "error")
})

test("empty or malformed success bodies cannot enable saving stale values", () => {
  const confirmed = completeContextSettingsLoad(createContextSettingsSession(), { message_count: 9 })
  for (const value of [undefined, null, [], {}, "invalid"]) {
    const session = completeContextSettingsLoad(beginContextSettingsLoad(confirmed), value)
    assert.equal(session.status, "error")
    assert.equal(session.confirmed.message_count, 9)
    assert.equal(session.draft.message_count, 9)
    assert.match(session.loadError, /некорректный ответ/)
  }
})
