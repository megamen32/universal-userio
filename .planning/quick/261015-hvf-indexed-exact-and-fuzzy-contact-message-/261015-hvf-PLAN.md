# Indexed contact and message search

Outcome: @semerindima resolves the real saved contact; exact username/phone/name/text results precede independently collapsible typo results. Search covers all retained messages through maintained SQLite indexes.

1. Reproduce missing metadata and old-message cutoff; trace ingestion and real consumer.
2. Add normalized contact data and maintained FTS/index tables, exact-first ranking and independently paged match groups.
3. Focused unit/integration (expected40s, emergency400s), UI build (expected40s/emergency400s); publish scoped main; rollback-first install and real browser44 search/collapse/reload.

Budget: existing project tests768MiB RAM/swap256MiB/CPU2/tasks128/IO10/temp128MiB; UI512MiB/swap0/CPU1/tasks64/IO10. Production existing384/512MiB/CPU1/tasks64 unchanged. Shared WIP in messenger.py and connector server.mjs stays protected.
