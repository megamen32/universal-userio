# Dashboard login

Started at 2026-08-28T14:03:43+03:00 (manual clock)
Estimate: 20-60 active minutes; active time not continuously measured.

Status: complete

Wanted result: `msg.bezrabotnyi.com` requires a UserIO login and shows data scoped to the authenticated user.
Shortest real canary: a fresh browser is redirected to login; `roomhacker` signs in and sees both Gmail addresses assigned to that user.
Smallest YAGNI slice: reuse the existing password verifier and hashed session store for a secure dashboard cookie, protect the dashboard/provider pages, and accept that cookie on `/v1/*`.
Discard now: external identity providers, account self-registration, password reset, session-management UI, roles UI, and frontend rebuild/polish.

Evidence so far:

- Live `/` returns `200` anonymously while `/mcp` correctly returns the OAuth discovery challenge.
- The live database has owner `roomhacker`; Gmail account rows `gmail-careviolan` and `gmail-megamen932` are enabled and owned by `user_owner`.
- Regression first failed because anonymous `/` still returned the dashboard; after the change, all 34 tests pass.
- Deployed `src/universal_userio/http_api.py` to `/opt/universal-userio`; runtime hash matches the committed source and `universal-userio.service` is active.
- Public anonymous `/` now returns `302 Location: /login`; `/mcp` still returns the OAuth protected-resource challenge.
- BrowserOS fresh-tab canary redirected to `/login`; `roomhacker` authenticated successfully and the expanded Email section showed `careviolan@gmail.com` and `megamen932@gmail.com`.

## Follow-up: public signup

Started at 2026-08-28T17:04:21+03:00 (manual clock)
Estimate: 15-45 active minutes; active time not continuously measured.
Status: complete

Wanted result: any new person can create an isolated UserIO login/password from the public auth screen.
Shortest real canary: register a unique browser user, land in an empty dashboard, sign out, and sign back in without seeing `roomhacker` data.
Smallest YAGNI slice: `/signup` form plus password-confirmation POST, hashed user creation without issuing a bearer token, and automatic dashboard session.
Discard now: email verification, CAPTCHA/rate limiting, password reset, social login, profile editing, and admin approval.

Evidence:

- Regression first failed because `/login` had no signup link.
- `/signup` now creates a hashed normal user without a bearer token and starts a user-scoped dashboard session.
- Full local suite passes: 35 tests.
- Deployed signup and rebuilt dashboard assets; `universal-userio.service` is active and public `/signup` returns `200`.
- Browser canary registered a unique user, landed in a dashboard with no chats/accounts, logged out through the visible `Выйти` button, and signed back in with the new credentials.
- The exact canary user was removed after verifying it owned no provider or conversation data; `roomhacker` was untouched.
