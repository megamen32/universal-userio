---
phase: 14-telegram-and-max-rich-operations
status: partial
verified: 2026-10-12
requirements-completed: [ACT-01, ACT-02, ACT-03]
score: 3/4 requirements
---

# Live rich-operation provider acceptance

UserIO source `eb14bb65f72ffe0b67b384fb742b38039b4e31e6` was published and
installed before these final actions. Runtime manifest verification succeeded.
The combined focused integration run passed 50 tests in 23.03 seconds, with
one existing pytest warning, helper exit0 and same-generation cleanup. No
browser, phone, SDK business request or historical action was replayed.

The current user explicitly separates UserIO work from Exmanager work. This
accepts the shared **provider contract** in ACT-02/03; it does not certify the
native Secretary MAX consumer or retire its transport. Those remain phase15.
Each scenario is focused integration, expected120/max600 seconds; source
ordinary verification retains its hard180-second aggregate deadline.

## Actual results

| Channel / operation | Observed result | Durable evidence |
|---|---|---|
| MAX reactions | Exact source117429468690188397: 👍 → 🔥 → removal; each fresh native reaction read agreed, original empty baseline restored | Private `.tmp/capabilities-rich-20261012/max-rich.json` |
| Telegram reactions | Exact QA source21: 👍 → 🔥 → removal through generic UserIO881 approval. Independent Telegram Helper identified the real sender `@deus_excode`; final read returned no reactions, restoring baseline | `tg-react-{add,change,remove}.json`, `tg-reaction-*-readback.json` |
| MAX sticker | New provider117429937932742855, fresh native sticker identity4830150843 matches the different real source117424822620729543 | `max-sticker-file-fixed.json` |
| Telegram sticker | New provider23; actual native sticker1391391008142393345 matches incoming QA reference22. Independent recipient download returned a sticker preview | `tg-sticker-file.json`, `tg-sticker23-recipient.json` |
| MAX new file | Provider117429938010356944; freshly downloaded171bytes/SHA256`3ed5bf98792661489a1d736538ee47e91326fd453715d583336b12fc116dbdfb` match staged bytes | `max-sticker-file-fixed.json` |
| Telegram new file | Provider24; independent recipient150bytes/SHA256`9575a93df64b1201edd2df15b1e2c9fa771d21f20ca65299b0fc3e78d7648c3d` match staged bytes | `tg-file24-independent-pass.json`, `tg-file24-recipient.json` |
| Telegram typing | Native start and stop commands accepted through generic source-bound approval | `tg-typing-provider.json`; recipient visibility **not observed** |
| MAX typing | Native start accepted; active=false uses advertised automatic native expiry and does not pretend to cancel immediately | `max-rich.json`; recipient visibility/expiry **not observed** |

All proof paths above are under the owning ignored private evidence directory;
do not publish their message bodies, credentials, native access keys or signed
packets. The table publishes only the minimum receipt metadata needed to audit
the claim. No mutation through Telegram Helper substitutes for a UserIO send:
Helper supplied incoming QA sticker22 without mention/reply and independently
read the actual outgoing deliveries; sender881 used the generic UserIO path.

## Defects fixed and retained failures

- MAX removes a reaction with native cancel179, not an empty emoji178; fresh
  native180 readback verifies the actual result.
- Received MAX global sticker IDs must be sent in native `attaches[].STICKER`.
  The former top-level shortcut expects a different set-local identity.
- Telegram history no longer loses a whole valid page because an identity-less
  native service record cannot be bound. Scope remains strict; omissions are
  explicit and direct resolve does not invent an author.
- An unavailable auxiliary MAX reaction read does not hide real message/media.
- The first wrong MAX sticker attempt remains durable `sending`/UNKNOWN at
  action7b017dbd… and was never repeated or cleared. Its failure remains separate
  from the different subsequent sticker success.
- `tg-sticker-file.json` ended with a readback assertion failure **after** both
  sends were accepted. Independent recipient download then proved correct file
  delivery without repeating it. The relay document reader returned its caption
  instead of bytes/text/SHA; Exmanager97 accepted this exact owned defect for its
  next source cycle. `tg-file24-read.json` preserves the failure, not a fake pass.

## Remaining proof and owners

ACT-04 remains open: native ACK does not prove recipient-visible typing or its
expiry. UserIO7bb owns the remaining provider/recipient observation; Exmanager97
owns the native SDK progress/fallback journey. Browser44 managed MCP is ready,
but its read-only native probe found no existing CDP Chrome. Browser88 has
foreign Chrome instances whose ownership/authentication are unproven; they were
not attached or changed. No controlled authenticated MAX recipient session was
supplied. New recipient UI work requires an owned finite managed44/88 route;
M1/Mini/100 fallback is forbidden. This is recorded in
`typing-recipient-surface.json`; no extra provider intent was issued for a
nonexistent observer.

Phase15 remains Exmanager-owned: actual native MAX source/reply/media/rich
journey, business/memory preservation and only then scoped transport retirement.
The relay document-content fix is tracked separately with that owner. It does
not invalidate independently confirmed file delivery and must not replay24.
