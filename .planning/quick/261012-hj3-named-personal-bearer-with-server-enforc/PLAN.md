---
quick_id: 261012-hj3
files_modified: [src/universal_userio/contracts.py, src/universal_userio/store.py, src/universal_userio/personal_tokens.py, src/universal_userio/token_permissions.py, src/universal_userio/mcp_surface.py, src/universal_userio/mcp_dispatch.py, src/universal_userio/http_api.py, src/universal_userio/static/connect-mcp.html, tests/test_readonly_tokens.py]
---
# Read-only personal Bearer for Secretary

Explicit owner request: separate named personal credential that can read current owned accounts/messages but cannot send, draft, approve, mark read, delete, claim, or manage accounts/settings/credentials. Existing full personal tokens, OAuth and service credentials retain behavior. Exmanager owns encrypted credential replacement and native Secretary acceptance.

1. Persist `scope=read_only` in existing token scope column; propagate only personal-token restriction to principal. Default `full_access` retains old behavior.
2. Filter MCP manifest and enforce an explicit pure-read allowlist at both dispatch entrypoints. HTTP denies mutations and credential/management reads for restricted principal.
3. Add token permission selection to existing page, focused RED/GREEN integration (expected 3s/max30s), compatibility checks within ordinary180s.
4. Publish source, rollback-first install and verify exact installed identity; real temporary read-only credential reads MAX+Telegram and denied mutations without provider actions, revoke it. Root owns final protected credential/API consumer.

No account session cloning, new authorization, provider sends, global capability changes, or foreign WIP.
