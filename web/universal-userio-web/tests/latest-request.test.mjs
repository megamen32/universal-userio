import assert from "node:assert/strict"
import test from "node:test"

import { LatestRequest } from "../src/latest-request.ts"

test("a late generic response cannot replace a newer account response", () => {
  const gate = new LatestRequest()
  let visibleSources = []
  const genericRequest = gate.begin()
  const accountRequest = gate.begin()

  if (gate.isCurrent(accountRequest)) visibleSources = ["gmail"]
  if (gate.isCurrent(genericRequest)) visibleSources = ["matrix", "telegram"]

  assert.deepEqual(visibleSources, ["gmail"])
})

test("disabling read invalidates an in-flight conversation request", () => {
  const gate = new LatestRequest()
  const inFlightRequest = gate.begin()

  gate.begin()

  assert.equal(gate.isCurrent(inFlightRequest), false)
})

test("secretary polling cannot invalidate a POST and a pre-POST GET cannot overwrite it", () => {
  const loadGate = new LatestRequest()
  const actionGate = new LatestRequest()
  const oldGet = loadGate.begin()

  // Starting the action invalidates every older GET. A separate action gate
  // means an interval tick cannot make the POST response stale.
  loadGate.begin()
  const post = actionGate.begin()
  const pollTick = loadGate.begin()

  assert.equal(actionGate.isCurrent(post), true)
  assert.equal(loadGate.isCurrent(oldGet), false)

  // Immediately before applying the POST response the UI invalidates any GET
  // that may have started while React was committing secretaryBusy=true.
  loadGate.begin()
  assert.equal(loadGate.isCurrent(pollTick), false)
  assert.equal(actionGate.isCurrent(post), true)
})
