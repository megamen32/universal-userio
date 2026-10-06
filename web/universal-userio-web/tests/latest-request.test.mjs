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
