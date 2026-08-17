# Universal UserIO MVP

Status: ready for repository publication

Accepted result: a separate business control plane that consumes canonical Inbox envelopes, stores a per-contact conversation, creates an AI draft, and emits a policy-bound NoticePlace reply only after approval.

Shortest canary: authenticated HTTP ingestion of a canonical Telegram/VK message produces a draft; authenticated approval emits exactly one scoped Outbox request. The automated HTTP canary passes locally.

Boundaries: Universal Inbox owns ingress/dedup; UserIO owns conversation and approval; NoticePlace owns delivery policy and provider credentials. Browser workers remain provider adapters for channels such as VK and WhatsApp.
