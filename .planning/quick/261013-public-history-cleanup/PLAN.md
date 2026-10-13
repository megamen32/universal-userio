---
type: execute
mode: quick
status: implemented
---
# Remove private account artifacts from public Git history

Direct owner authorization covers git filter-repo, synthetic test identities, full reachable-history scan and force-push of existing public refs. Preserve runtime, foreign working edits and private rollback evidence.

1. Private verified Git bundle and WIP/index inventory. Export to a bare review artifact without resetting an existing checkout.
2. Remove private agent sessions/operator reports from every revision; replace known real identities with synthetic values while preserving identifier type/range.
3. Scan all rewritten refs against protected current secrets and identity replacements; run affected fixture tests, preserve failed preliminary checks.
4. Adopt refs/index with CAS and preserve working edits; publish existing heads/tags atomically with exact remote leases; independently read back the refs.
5. GitHub-managed PR/cache references require a separate provider cleanup. Prepare a private support request and do not claim that boundary closed.

Focused integration: history rewrite/scan expected30s/emergency120s under the existing sealed512/768MiB,CPU1,tasks128,swap0 profile. Fixture verification measured66s initial suite and10s corrective delta; Node12cases below1s. No service restart, model, message or phone action.
