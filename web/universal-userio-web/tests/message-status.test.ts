import assert from "node:assert/strict"
import { test } from "node:test"
import { outgoingMessageStatus } from "../src/message-status.ts"

test("a sent outgoing message has one check until the provider confirms it was read", () => {
  assert.equal(outgoingMessageStatus("telegram", true, false), "sent")
  assert.equal(outgoingMessageStatus("telegram", true, null), "sent")
  assert.equal(outgoingMessageStatus("telegram", false, true), null)
  assert.equal(outgoingMessageStatus("gmail", true, true), null)
  assert.equal(outgoingMessageStatus("phone", true, true), null)
})

test("a provider read receipt changes an outgoing message to two checks", () => {
  assert.equal(outgoingMessageStatus("telegram", true, true), "read")
  assert.equal(outgoingMessageStatus("telegram", true, 1), "read")
  assert.equal(outgoingMessageStatus("matrix:account-a", true, 0), "sent")
})
