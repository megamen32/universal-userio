---
status: partial
source_commit: 047f304
---

# One account screen installed

Inbox now has one «Аккаунты · подключить и настроить» entry. Legacy mail/Matrix/VK
links share /accounts, one provider form at a time, short inline steps, optional
advanced fields, Gmail/Yandex presets and same-page success/next account.
Current-user connections appear separately from the service selector. Phone and
Telegram return to the same account screen; VK ID dead-end removed from default
flow. Existing callback, API and account histories preserved.

NEW Telegram browser slots now resolve the actual personal principal and persist
only slot→user. State/code/password/start are scoped to that user; registration
uses its transient cookie. Service credentials and legacy sessions unchanged;
new users do not inherit the operator's saved 2FA password. Personal registration
permits normal reply; legacy operator admission stays unchanged. Failed personal
registration does not start ingress as the operator.

[Live UI proof](VERIFICATION.md) covers desktop/mobile, ten guide panels, normal
fixture SMS enrollment/reload/disconnect, and actual empty Telegram user view.
Remaining self-service provider gaps moved to phase16, never falsely closed.
