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
