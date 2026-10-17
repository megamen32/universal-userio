import assert from "node:assert/strict"
import { test } from "node:test"
import { groupMatrixChatRows } from "../src/matrix-chat-groups.ts"

const row = (values: Partial<{
  id: string; source: string; account_ref: string; peer_id: string; sender: string; unread_count: number
}> = {}) => ({
  id: "conv-a", source: "matrix:account-a", account_ref: "matrix:account-a",
  peer_id: "!room:chat.example", sender: "@elena:chat.example", unread_count: 0,
  ...values,
})

test("collapse mirrored Matrix room copies and keep account unread counts separate", () => {
  const first = row({ unread_count: 72 })
  const second = row({ id: "conv-b", source: "matrix:account-b", account_ref: "matrix:account-b", unread_count: 54 })
  const otherRoom = row({ id: "conv-c", account_ref: "matrix:account-c", source: "matrix:account-c", peer_id: "!other:chat.example", unread_count: 9 })
  const otherChannel = row({ id: "conv-d", account_ref: "matrix:account-d", source: "telegram", unread_count: 4 })

  const grouped = groupMatrixChatRows([first, second, otherRoom, otherChannel])

  assert.equal(grouped.length, 3)
  assert.equal(grouped[0].id, first.id)
  assert.deepEqual(grouped[0].account_refs, ["matrix:account-a", "matrix:account-b"])
  assert.deepEqual(grouped[0].account_unread_counts, {
    "matrix:account-a": 72, "matrix:account-b": 54,
  })
  assert.deepEqual(grouped[0].account_conversation_ids, {
    "matrix:account-a": "conv-a", "matrix:account-b": "conv-b",
  })
  assert.equal(grouped[0].unread_count, 72)
  assert.deepEqual(grouped.slice(1).map(chat => chat.id), ["conv-c", "conv-d"])
})

test("do not merge Matrix rows without an exact room id", () => {
  const grouped = groupMatrixChatRows([
    row({ id: "conv-a", peer_id: "" }),
    row({ id: "conv-b", account_ref: "matrix:account-b", source: "matrix:account-b", peer_id: "" }),
  ])
  assert.deepEqual(grouped.map(chat => chat.id), ["conv-a", "conv-b"])
})

test("do not discard same-account rows even when the room id matches", () => {
  const grouped = groupMatrixChatRows([
    row({ id: "conv-a" }),
    row({ id: "conv-b", source: "matrix:account-a:legacy" }),
  ])
  assert.deepEqual(grouped.map(chat => chat.id), ["conv-a", "conv-b"])
})
