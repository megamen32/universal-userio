# Dot Telegram mirror: text/media/reply acceptance plan

Status: diagnostic, not deployed. Existing source PR: #6, draft at 18a1065.

## Confirmed
- Isolated worktree, 51 selected offline tests pass.
- Plain text ingress awaits all active DotMediaIngress.waitForPending() promises. A stalled media flush can delay unrelated text. This is a hypothesis, not a confirmed production cause.
- Never replay send_uncertain, submitted_unconfirmed, or echo_unknown without reconciling exact receipt.

## Next steps
1. Find deployed bridge SHA, sole poller, binding, journal metadata and exact page URL; do not expose secrets or private message content.
2. Correlate one reported missed text source ID to journal and browser echo. Distinguish waiting for media from unavailable transport.
3. If proven, fix stalled media/text sequencing with bounded, deterministic tests preserving source order.
4. Reconcile unknown sends before any authorized test messages. Test one uniquely identified text, media and genuine source-bound reply, confirming both provider receipt and dot echo.
5. Run full suite, review existing PR #6, deploy only through gates and verify release readback.

Constraints: no new bot, no token extraction, no second poller, no broker/proxy/lifecycle changes, no duplicate Autogram notifications.
