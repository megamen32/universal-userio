# Shared dot / ExManager question wire contract (source-only)

2026-11-02 21:02 UTC. Goal: unify two existing consumers to one already-merged UserIO question lease and messenger_actions send fence. Keep Todo card 3122894f-e7ac-4404-9271-f90e6c9a6f5e in review. No production rollout/lease, no Hub/UserIO restart, no messages or new credentials.

Base: isolated UserIO worktree d5c1eb0 containing PR13 0e45337 and PR15 defeae4. This branch adds only docs/verification/dot-manager-shared-contract-20261102.md, tests/test_question_consumer_wire_contract.py and this brief. No product file/other test modifications.

Canonical contract: namespace userio.messenger.question.*, wire revision (not source_revision), reconcile (not reconcile_delivery), success nested under question, canonical account|peer:providerMessage source_message_id, result_ref messenger:<persisted action_id>, no second provider fence or direct mirror send for claimed question. Existing service-token-only dot HTTP POST /v1/questions/operation is separate from MCP; no caller-supplied holder/account/permissions. GET /v1/workspace/events provides event_seq; POST /v1/messages does not. Unknown never auto-replayed; a later journal read uses a new idempotency ID.

Offline receipts on clean installed-base d5c1eb0: existing question/native SQLite suite 29/29 PASS, strict consumer wire schema/real SQLite RPC suite 2/2 PASS; both have the pre-existing PytestConfigWarning about asyncio_default_fixture_loop_scope. No real providers.

PR12 adds an outdated second question_coordination_adapter/_question_fence, overlaps the merged PR13 and must not be merged unchanged. PR17 ExManager consumer uses the wrong tool namespace, field, reconcile name and response envelope; its owner fixes its two new Go files. Mirror successor 6ae8637c owns relay/mirror/server, ExManager owner 6ae780de owns consumer Go scope; logging owner 6ae895e6 owns only passive trace. PR16 1d70cb8 is tests-only with 71/71 PASS and scan success, but is not merged and has no independent GitHub review/approval at last read.

Human review required before integration/merge. No active release lease requested and no 11 genuine runtime acceptance cases claimed.
