# LHC bug: ChatGPT adapter send() contract missing

- ID: USERIO-20261102-CHATGPT-ADAPTER-SEND-DRAFT
- Status: FIXED_IN_CANDIDATE; production acceptance pending
- Severity: reply draft contract broken (ChatGPT channels)
- Component: src/universal_userio/adapters.py
- Discovery: 2026-11-02
- Source ownership: UserIO ChatGPT observability worktree/PR #15 only; no service, mirror, claims, ingress or OAuth files.

## RED evidence

- tests/test_chatgpt_web.py::test_send_anchors_draft_in_store:
  AttributeError: ChatGPTWebChannelAdapter has no attribute send.
- tests/test_multi_user.py::test_chatgpt_cdp_adapter_reads_page_visible_chats_without_provider_credentials:
  AttributeError: ChatGPTCDPChannelAdapter has no attribute send.
- Both missing methods are present in current origin/main baseline as omissions, not newly introduced by logging.

## Required behavior

- send(chat_id,text) stages one proposed, user-scoped draft only; it never submits to ChatGPT or a CDP sidecar.
- Resolve exactly the requested chat with existing read/identity and bind its authentic latest message as a reconciliation ingress without triggering AI inbound listeners.
- Use existing UserIO store/draft and approval gates, preserving one-shot provider send/replay/claim semantics.
- Do not weaken tenant ACL/route policy, alter other adapters, or add a provider test request.

## Acceptance

- [x] Both original RED tests pass without skip, xfail or disabling (2/2 PASS).
- [x] Offline regression of stage/approve separation, replay, tenant ACL and no send on staging (4/4 PASS).
- [x] Targeted tests: 20/20 PASS across web adapter, trace and CDP mock; Python compile and diff --check PASS.
- [ ] Review, published commit, guarded backend merge/release and readback.

## Remaining native release gate

- Standalone Herder lease status for canonical UserIO repo: lease=null, deploymentInProgress=false, nextFence=1, owners=[].
- Caller session identity lookup failed with caller_session_unavailable; no synthetic session ID may be used for lease acquire.
- PR #15 must pass independent review and protected merge, then guarded container_release.py backend replacement and passive production readback.
