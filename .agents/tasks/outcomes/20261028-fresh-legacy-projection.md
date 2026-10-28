# Fresh mirror lookup of preserved legacy Telegram rows

Lead independently proved stored history and channel refresh after the first
release, then found `messenger.history(refresh=true)` still failed with
`fresh Telegram projection missing`. This is a separate alias lookup defect:
the native connector successfully imports the row and returns its numeric ID,
but the mirror provider looked up only its account-scoped storage spelling.
Ingestion intentionally retains the older `peer:ID` spelling.

The same defect affected exact native-message resolution. A shared bounded
lookup now tries only `account|peer:ID` and `peer:ID`, under the existing exact
user/account/conversation/peer authorization. A found row in a different
conversation fails closed. Provider projection validation accepts only those
two exact spellings and preserves the original durable message ID. It never
uses a stored row as proof of provider freshness: the successful native receipt
and returned numeric ID are still required before lookup.

Scope: `messenger_telegram_mirror.py` and NEW
`tests/test_telegram_mirror_legacy_projection.py`. No Telegram wire/API calls,
sessions, send actions or read acknowledgement behavior changed.

RED: the new fast regression produced 2 failed / 2 passed, reproducing both
fresh-history and exact-resolve failures. GREEN: the new tests plus existing
native message and stored legacy tests returned 20 passed in 23.77 seconds.
Both foreign-conversation lookups still fail closed; no rows are renamed or
duplicated. Existing pytest unknown-option warning remains unchanged.

Lead owns independent installed consumer proof. This receipt records source
evidence; the second one-file overlay is prepared separately on the accepted
first image, preserving all earlier fixes and configuration.
