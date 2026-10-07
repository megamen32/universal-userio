# Secretary same-channel reply contract

Owner request: SMS inbound replies through the same SMS account/number;
Matrix inbound replies through the same Matrix account/room. No Telegram
fallback. Parent coordinates the active cache slice before shared-file edits.

Current observation: eXmanager provides explicit owner-scoped workspace sync
and outbound draft/approval tools, not a native bidirectional consumer.
SMS account exposes reply; Matrix account is read-only and has no outbox.
Matrix events currently lack explicit peer room and collide by sender.

Prepared new, non-overlapping files `same_channel_reply.py` and its focused
contract tests. They have no side effects and are not yet wired into service,
store, ingress or MCP. They are preparatory code, not delivery completion.

Required immutable event fields: user-scoped seq, source, account_ref,
conversation_id, peer_id, sender identity, original message_id, direction,
conversation_kind, sender_is_bot, body_truncated. Preserve quoted reply context
separately, but ordinary outgoing reply targets the incoming event itself.
Matrix uses actual room/event IDs, never a guessed room from sender or legacy
composite IDs; canonical UserIO message_id stays unchanged for deduplication.
Raw provider_message_id is separate and Matrix reply relation targets it.
SMS uses exact originating number and canonical account ID. Source body SHA-256
and edited_at participate in the snapshot: edits invalidate stale responses.

Consumer requires server-owned event-scoped ordinary-reply consent plus active
worker lease; read access, event ID or connected account alone does not authorize
sending. Operation carries only event_seq, exact expected provenance, reply text
and stable request_id, not arbitrary recipients. Existing general outbound
draft approval remains authoritative for a new destination or other action.
Provider send must revalidate capability, atomically persist a claim, preserve
ambiguous outcomes, and never retry a possibly accepted send with a new key.

No shared dirty UserIO files edited. No provider calls, SMS/Matrix sends, S21
control, build, commit or deployment performed in this preparation.
Parent owns publication and activation after cache-owner integration.

Parent explicitly authorized the tiny focused contract check:
`PYTHONPATH=src python3 -m unittest discover -s tests -p test_same_channel_reply_contract.py`.
Result: four tests passed in 0.001 seconds, exit 0. No full suite started.

## Exact remaining blockers

Full native Secretary channel ingress requires a supported generic Airlock
bridge contract. Installed core supports only Telegram/userbot ingress; do not
forge an owner web conversation or present a separate one-step GenerateText
responder as completed Secretary integration. Parent owns explicit maintainer
coordination after this concrete source-account/event/receipt contract review.
Matrix direct outbox, source-account/room/provider-event provenance and reply
capability remain unconnected. `/opt/universal-userio` shared service/store/
Matrix source contains active foreign cache/channel changes; wait for the
authorized cache owner to publish and define integration boundaries before
editing or deploying those files. The prepared new files remain visible in this
dirty canonical main checkout, not a branch or stash, and parent owns delivery.

## Server-100 preservation publication, 2026-10-07

Prepared source/test were restored from the old Airlock stream without
overwriting any cache-owner file. Current UserIO base is `539c9c2`. Only these
three contract paths belong to Secretary owner
`01a11744-d8f4-7293-87be-602a32a41aec`; shared service/http/store/UI remain with
UserIO owner `01a11747-7bb9-7bc0-8c0a-ce64db49105d`.

Exact source SHA256: `a4a1a5ebf4a5ef14b3ba35e9481a0ae76a58fbbe7c3b28dfc211c99ef28b2944`;
test SHA256: `839abbe33c07120124bc4b8c7702ad66f565aa47d0b91cc18ad3ff476848c9a1`.
Both match the originally tested `/opt/universal-userio` preparation byte for
byte. Reused the recorded four pure contract tests; both current files also
pass bounded AST parsing. No new package import, pytest, build, provider test
or service restart was attempted under the closed resource gate. These checks
verify the unchanged standalone validator, not the cache owner's late seven
backend tests or any integration.

The parent publishes only this preparation on `main` so that the current
canonical checkout does not retain our untracked work. The unrelated existing
`universal-userio-airlock` worktree was preserved without edits. Runtime bytes
were read only; no installation or activation occurs in this publication.

Telegram native-preview ordinary reply is separately delivered in Exmanager
source `db4a355`, with post-canary archive `caa4e4b`; live isolated reply
26→27→28 created exactly one card and filtered URL. SMS/Matrix remains blocked
on the supported native bridge contract and the coordinated UserIO integration
described above. Mac reverse SSH is unavailable; no Mac dirty file was changed
or synchronized. Canonical project tracker is this file; do not count closed
upstream issue115, the pure helper, or Telegram success as SMS/Matrix delivery.
