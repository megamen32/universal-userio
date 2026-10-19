import { strict as assert } from "node:assert"
import { test } from "node:test"
import { canEditMaxText } from "../src/max-message.ts"

test("existing MAX and VK attachment edit rules remain intact", () => {
  assert.equal(canEditMaxText([]), true)
  assert.equal(canEditMaxText([{ kind: "share" }]), true)
  assert.equal(canEditMaxText([{ kind: "photo" }]), false)
})
