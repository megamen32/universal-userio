import test from "node:test"
import assert from "node:assert/strict"
import { assertDeclaredMediaWithinLimit, boundedMediaLimit, MAX_MCP_MEDIA_BYTES } from "./media-download-limit.mjs"

test("MCP media limit stays within the existing four MiB cap", () => {
  assert.equal(boundedMediaLimit(1024), 1024)
  assert.equal(boundedMediaLimit(), null)
  assert.throws(() => boundedMediaLimit(MAX_MCP_MEDIA_BYTES + 1), /invalid media download limit/)
  assert.throws(() => boundedMediaLimit(-1), /invalid media download limit/)
})

test("rejects a Telegram document whose declared TL size exceeds the requested bound", () => {
  assert.throws(() => assertDeclaredMediaWithinLimit({ media: { document: { size: 4097 } } }, 4096), /exceeds/)
  assert.doesNotThrow(() => assertDeclaredMediaWithinLimit({ media: { document: { size: 4096 } } }, 4096))
  assert.doesNotThrow(() => assertDeclaredMediaWithinLimit({ media: { photo: {} } }, 4096))
  assert.doesNotThrow(() => assertDeclaredMediaWithinLimit({}, null))
})
