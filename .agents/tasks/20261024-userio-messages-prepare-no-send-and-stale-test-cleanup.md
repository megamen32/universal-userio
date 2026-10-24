# UserIO messages.prepare must not send, and the stale prepare/send test lane

Result: **source fix published, stale-test lane in progress.** The mutual Telegram
canary of 2026-10-24 proved ordinary text delivery in both directions between the
owner's two connected Telegram accounts and exposed a measured contract defect:
two calls to the public `userio.messages.prepare` created two real Telegram
messages ("Черновик ждёт подтверждения"), even though the tool documents
"Nothing is sent". A draft notice is itself an outbound provider message, so the
prepared draft reached the peer before it was ever sent, and the notice for a
reverse-direction draft landed in the opposite dialog.

## Accepted outcome and canary

Prepare is a local compose step: it stores exactly one durable draft and returns
it for review, and it must produce **no outbound provider action** — not the
draft itself and not an owner notification. The only delivery path is the
reviewed approval of `userio.messages.send`, which keeps its exact-snapshot
confirmation (`expected_text`, `expected_chat_id`, `expected_attachments` are
mandatory) and its store-level idempotency: a repeated send replays the stored
receipt instead of delivering twice. An uncertain delivery keeps the claim and
blocks a retry. The AI draft-approval flow (`propose_for_approval`,
`receive_and_plan`) still notifies the owner, because those drafts genuinely await
a human decision.

Canary acceptance for the source lane: the new regression fails on the current
code and passes on the fixed code; the two named delivery files
(`tests/test_telegram_history_delivery.py`,
`tests/test_telegram_delivery_reconciliation.py`) stay green; no provider,
network, deploy or runtime operation is part of the source work.

## Completed source commit and evidence

Published commit `5f58aa825feceee5350a1eb05a443f56a227c3ad` on `origin/main`
(fast-forward from `c73eb1c092e64c6eb4c02824cc6f649961761c69`, no force), exactly
two paths: `M src/universal_userio/contact_start.py` and
`A tests/test_messages_prepare_no_send.py`. The fix is one line plus a contract
docstring: `ContactStarter.prepare` no longer asks for the owner notification by
default, so none of the four public compose routes (MCP surface, MCP dispatcher,
scoped messenger dispatch, HTTP `/v1/messages/prepare`) reaches the provider.

RED: `pytest tests/test_messages_prepare_no_send.py` → 2 failed on
`telegram.sent == []`, showing the real notice body
(`chat_id 540308572`, `account_ref telegram:8810909089`) — the same two messages
the live canary delivered. GREEN: the same file → 2 passed. The named delivery
files → 48 passed. The regression uses the production
`TelegramDraftApprovalNotifier` over a recording fake transport, so no provider
is touched.

## Completed lane: stale prepare/send tests

The upstream API was narrowed to exactly `userio.messages.prepare` and
`userio.messages.send` (commit `d49f78d`, rename `31469d4`), but four test
files still asserted the removed surface. Measured on the current `origin/main`
(`fcbb104fb1daba4d6de61aa0b89f160578c5fd5b`): **34 failed, 75 passed**. Every
failure was a stale expectation, not a product defect:
`create_manual_draft` (renamed to `prepare_manual_message`), `ContactStarter.start`
(replaced by prepare + send), `userio.draft.approve_send` and
`userio.channels.send_draft` (removed), and the `first_contact` capability
operation `start` (now `prepare`). Upstream had not corrected these files.

The rewrite keeps the behavioral coverage and moves it onto the current public
contract: `userio.messages.prepare` composes the draft, `userio.messages.send`
approves it with the exact snapshot, and the send replay, the receipt checks and
the capability denials are unchanged. The obsolete `approval_hint` /
`manual_approval_only` expectations are dropped because that hint surface no
longer exists. Legacy tool names are never restored and no production source is
touched by this lane.

One current rule shaped the fixtures: **`userio.messages.prepare` is
account-bound** - the chat must carry a registered, reply-capable provider
account. The removed `userio.draft.create` was not, so each affected test now
registers the exact provider account and binds the incoming conversation to that
`account_ref`. Where a test could not legitimately send under the current
capability rules (an unconfigured provider), the capability denial is kept and
asserted in the new result shape.

Result in the canonical checkout: the four updated files, the new regression and
the two named delivery files run together — **159 passed, 0 failed**. The lane is
published as a test-only commit on the fetched `origin/main` tip
(`80df88dda30c65d12682f8ce59b317212bb6bb46`) without modifying the real index or
the worktree.

Owned paths for the lane: `tests/test_mcp_surface.py`, `tests/test_service.py`,
`tests/test_first_contact_delivery.py`, `tests/test_http_api.py` and this record.
The dirty checkout keeps foreign WIP in `src/universal_userio/adapters.py` and
`deploy/telegram-qr-connector/server.mjs` untouched (exact copies preserved under
`.tmp/pre-merge-20261024/`).

## Next step: backend-only release and post-install canary

The source fix is not deployed. The next action is a **backend-only release** of
the UserIO backend image that carries commit `5f58aa8` (no frontend asset
rebuild, no Telegram connector change), followed by a post-install canary on the
owner's two accounts: one `userio.messages.prepare` per direction must produce
**no** provider message and no Telegram notice, while `userio.messages.send` must
deliver the prepared text exactly once with a determinate receipt and a
receiver-side readback. The two notices already delivered to Nikita on
2026-10-24 are not retractable and remain in the secretary-to-nikita dialog; the
Secretary auto-reply "Начал обработку…" is recorded as existing automation, not
a defect.
