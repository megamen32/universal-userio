import assert from "node:assert/strict"
import test from "node:test"
import { SecretaryRequests } from "../src/secretary-requests.ts"

test("a GET started before deep POST cannot overwrite accepted after POST finishes", () => {
  const gate = new SecretaryRequests()
  const staleGet = gate.beginRead()
  const post = gate.beginAction()
  assert.notEqual(staleGet, null)
  assert.notEqual(post, null)
  assert.equal(gate.beginRead(), null)
  assert.equal(gate.isCurrentAction(post), true)
  assert.equal(gate.finishAction(post), true)
  assert.equal(gate.isCurrentRead(staleGet), false)
  const nextPoll = gate.beginRead()
  assert.equal(gate.isCurrentRead(nextPoll), true)
})

test("switching chat cancels an action without clearing a new chat's busy state", () => {
  const gate = new SecretaryRequests()
  const oldPost = gate.beginAction()
  gate.reset()
  const newPost = gate.beginAction()
  assert.equal(gate.isCurrentAction(oldPost), false)
  assert.equal(gate.finishAction(oldPost), false)
  assert.equal(gate.beginRead(), null)
  assert.equal(gate.isCurrentAction(newPost), true)
  assert.equal(gate.finishAction(newPost), true)
})

test("late load failure cannot replace a newer successful load", () => {
  const gate = new SecretaryRequests()
  const failedGet = gate.beginRead()
  const successfulGet = gate.beginRead()
  assert.equal(gate.isCurrentRead(failedGet), false)
  assert.equal(gate.isCurrentRead(successfulGet), true)
})
