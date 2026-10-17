import assert from "node:assert/strict"
import { test } from "node:test"
import { outgoingMessageStatus } from "../src/message-status.ts"

test("a sent outgoing message has one check until the provider confirms it was read", () => {
  assert.equal(outgoingMessageStatus(true, false), "sent")
  assert.equal(outgoingMessageStatus(true, null), "sent")
  assert.equal(outgoingMessageStatus(false, true), null)
})

test("a provider read receipt changes an outgoing message to two checks", () => {
  assert.equal(outgoingMessageStatus(true, true), "read")
  assert.equal(outgoingMessageStatus(true, 1), "read")
  assert.equal(outgoingMessageStatus(true, 0), "sent")
})
