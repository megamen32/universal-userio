# MAX API research — 2026-10-11

Official https://dev.max.ru/docs-api is for bot tokens, not personal history. Maintained https://github.com/MaxApiTeam/PyMax documents WebClient QR authorization, persisted sessions, events/history/send, TCP and WebSocket. Existing UserIO client already implements OneMe v11, so reuse its small transport and verify actual frames before introducing a library migration. Source references: PyMax auth/qr.py, api/auth/service.py, protocol/enums.py; https://github.com/pr0bel1230/max-api-docs. Protocol is unofficial and must fail explicitly on drift.

Existing gaps: no configured token/unit; ingress discards outgoing and chat/provider timestamp; channel always out=False; command response ignores seq and drops interleaved dispatch; reconnect/shutdown lifecycle fragile. These are delivery work, not validated capabilities.
