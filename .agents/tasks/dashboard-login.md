# Dashboard login

Started at 2026-08-28T14:03:43+03:00 (manual clock)
Estimate: 20-60 active minutes; active time not continuously measured.

Status: implementation verified locally; deployment and browser canary pending

Wanted result: `msg.bezrabotnyi.com` requires a UserIO login and shows data scoped to the authenticated user.
Shortest real canary: a fresh browser is redirected to login; `roomhacker` signs in and sees both Gmail addresses assigned to that user.
Smallest YAGNI slice: reuse the existing password verifier and hashed session store for a secure dashboard cookie, protect the dashboard/provider pages, and accept that cookie on `/v1/*`.
Discard now: external identity providers, account self-registration, password reset, session-management UI, roles UI, and frontend rebuild/polish.

Evidence so far:

- Live `/` returns `200` anonymously while `/mcp` correctly returns the OAuth discovery challenge.
- The live database has owner `roomhacker`; Gmail account rows `gmail-careviolan` and `gmail-megamen932` are enabled and owned by `user_owner`.
- Regression first failed because anonymous `/` still returned the dashboard; after the change, all 34 tests pass.
