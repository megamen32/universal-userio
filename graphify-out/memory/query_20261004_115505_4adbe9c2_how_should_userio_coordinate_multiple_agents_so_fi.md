---
type: "architecture"
date: "2026-10-04T11:55:05.697593+00:00"
question: "How should UserIO coordinate multiple agents so first claim wins and Hermes delivers through the Telegram home chat?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["userio", "workspace_events", "poll", "lease", "claim", "agent-herder", "hermes", "telegram"]
---

# Q: How should UserIO coordinate multiple agents so first claim wins and Hermes delivers through the Telegram home chat?

## Answer

Use a durable user-scoped workspace claim table with an atomic ten-minute lease, opaque ownership token, renewal, done/failed transitions, and an audit log. Agent Herder launches one bounded Hermes job; the dispatcher sends only the marked final result to the exact configured Telegram home chat and thread.

## Outcome

- Signal: useful

## Source Nodes

- userio
- workspace_events
- poll
- lease
- claim
- agent-herder
- hermes
- telegram