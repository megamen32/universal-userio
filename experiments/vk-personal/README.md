# VK Personal — server-only authentication proof

Status: experimental, NOT deployed to UserIO.

This is an independently written, read-only test of ordinary VK personal
messages using the request shape observed in Fast Messenger. That project is
GPLv3 and its source code is not copied into this prototype. The VK Messenger
client id is a client identity, NOT authorization for UserIO.

Findings:
- Verified with real VK: auth.getAnonymToken returns an anonymous bearer
  without a user's password. auth.exchangeSilentAuthToken accepts its documented
  request form and rejects a deliberately invalid test token (error 100).
- Verified with simulated responses: the OAuth result containing a silent_token
  payload is parsed; VK can exchange it for a personal bearer; users.get,
  messages.getConversations, and messages.getHistory can be checked without
  writing a message or marking it read.
- Still unverified with a fresh user-approved session: actual exchange
  success, complete personal history, browser-free restart and live updates.
  No claims of production-ready VK Personal should be made yet.

DO NOT paste any personal callback URL or bearer into ChatGPT, GitHub, MCP
arguments, bug reports or shell command arguments. The previously shared
short-lived token must not be reused. For a live POC, the owner must obtain
a fresh OAuth result and paste it only into the interactive hidden prompt.

Run from a private terminal on the UserIO host:

    cd /var/tmp/userio-vk-silent-session-poc-20261012
    python3 experiments/vk-personal/verify_transport.py --anonymous-probe
    python3 experiments/vk-personal/verify_transport.py --save /var/tmp/userio-vk-personal-poc-private/session.json

The second command requests a fresh VK callback URL with hidden input.
It checks owner identity, dialog list and a history sample before optionally
saving a temporary proof bearer. This saved credential is restricted to mode
0600 inside a 0700 directory and expires locally even if VK omits expiry.
Supply --expected-vk-id followed by your numeric ID if several profiles might
be logged in.

Close the authorization browser completely, then use another terminal
process on the *same UserIO server* to prove the session works without Chrome:

    python3 experiments/vk-personal/verify_transport.py --resume /var/tmp/userio-vk-personal-poc-private/session.json

Delete the temporary secret immediately after testing:

    python3 experiments/vk-personal/verify_transport.py --clear-session /var/tmp/userio-vk-personal-poc-private/session.json

This first proof only samples at most five chats and the latest ten messages
of one chat. It does not prove full historical paging, edits or attachments.
Before mass rollout, additionally prove new inbound delivery, carefully
authorized send only to a confirmed test recipient, recovery after restart,
user revocation, tenant isolation and lifecycle of refreshed credentials.
Neither a shared browser profile nor a community bot token suffices.

Radist's separate vk_direct QR API is a candidate fallback. It requires a
company-scoped API key; cost and tenant isolation for a SaaS are not yet
confirmed and are not used by this POC.

## Correct exchange field mapping and stage separation

Observed reference: Fast Messenger commit `f422b54`, Android entry point
`LoginViewModel.kt` and request mapping in `OAuthRequest.kt`.
This independently implemented POC uses only fixed VK API method names and
argument shapes from the reference; it does not include GPLv3 source code.

- `client_id=51453752`: the VK Messenger client identifier involved in the
  OAuth attempt and the anonymous session request. VK API version in this
  test is `5.263`.
- `auth.getAnonymToken` with this client ID, **without client_secret**,
  succeeded against the actual VK endpoint in a read-only preliminary test.
  Fast Messenger sends `client_secret` as well. If the operator has an
  authorized value, `VK_MESSENGER_CLIENT_SECRET` can supply it privately.
  This observation does not establish that all VK clients work without it.
- `auth.exchangeSilentAuthToken` request fields have an unintuitive
  meaning: `access_token` is the **anonymous token** from the first step;
  `token` is the short-lived **silent token** from the browser callback;
  `uuid` is the UUID of **that exact authorization**, including an empty
  UUID only if VK returned it empty. Never substitute another attempt's UUID.
- Only after a *positive* exchange do we have a **user access token**.
  Its `expires_in` is separately tracked; the input silent token's
  `ttl=600` does not determine the resulting session lifetime.
- A denied `auth.exchangeSilentAuthToken` is reported as an exchange denial;
  a denied `messages.getConversations` or `messages.getHistory` is
  reported as a **message permission** denial. The API method and numeric
  error code are printed, not credentials or server response strings.
- Only after a successful personal chat/history proof, the optional
  `--inspect-common-token` can request `auth.getExchangeToken` and report
  whether the expected account has a `common_token`. The token value is
  neither returned nor saved, and its meaning as a refresh token is
  **explicitly unverified**.

## Binding attempts to a UserIO principal

`attempts.py` is an experimental one-time, owner-bound state registry for
a future UI. It produces a random `attempt_id` and OAuth `state`, records
the authenticated UserIO user and optional expected VK user ID, expires in
600 seconds, and enforces a matching callback state and single use.

**It is intentionally NOT enabled as a production OAuth callback.**
The observed `#payload=silent_token` URL lacked `state`; no experiment
has established that this OAuth route actually echoes our supplied state.
If it does not, an automatic server callback that *pretends* to have verified
state would be unsafe. Our current private CLI flow instead requires the
owner to paste the result into their own hidden terminal prompt, then validates
the actual VK ID directly against VK. Any future browser/web UI needs a
provider-supported state/callback binding or an explicit, authenticated,
owner-confirmed manual-import flow. Never infer that possession of a VK ID
by itself proves ownership of a callback.

The proof does **not** yet demonstrate reading a personal VK account. That
remains blocked on one fresh owner-authorized test, which cannot be completed
with a previously posted or expired token.

## Strict history gate added

The POC now fails if VK returns an empty personal dialog list or if the
selected conversation has no messages. A working profile or a success response
from messages.getConversations alone will not count as a completed proof.

For a known inactive conversation, optionally supply
`--history-peer-id <NUMERIC_PEER_ID>`. The probe selects that peer for
messages.getHistory rather than the latest chat. The ID is not a secret,
but avoid copying an actual chat identifier into public issue reports.
The check does not mutate provider read state and does not print message text.

## Security review follow-up (2026-10-12)

The proof now stores the VK ID confirmed by the real users.get response,
not a caller-supplied or missing callback ID. Subsequent resume validates
this saved identity even when no expected ID is provided. A session with an
empty saved owner is rejected.

For a noninteractive GPTAdmin flow, create a one-time Hub secret_request.
The browser form value stays out of chat and shell command arguments. On
execution use shell_exec.secret_env to inject the opaque secret_ref into the
USERIO_VK_LOGIN_RESULT environment name, and run verify_transport.py with
--from-secret-env and the expected VK ID. It consumes the environment value
once and never prints the authorization result.

For interactive fallback, getpass.GetPassWarning is treated as an error so
the URL can never be entered into an echoing terminal by accident.

When an expired proof file is encountered on resume, the local file is
deleted. This does not revoke the session on VK's servers; account security
settings must be used for provider-side revocation.

The proof requires at least one message from a personal dialog; no empty
history is accepted as a complete success. The historical peer can be selected
with --history-peer-id. Production UserIO is unchanged.
