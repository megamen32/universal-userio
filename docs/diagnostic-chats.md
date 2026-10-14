# Тестовые чаты UserIO

По прямому поручению владельца14Oct диагностический чат существует в UserIO,
а интегрирующее приложение использует его, не подделывая Telegram-входящие.
Это обычная conversation/source=diagnostic с обычными messages в том же store.
Отдельные таблицы содержат только test subject metadata/idempotency, не вторую
историю переписки. Readback/reopen сохраняют входы и ответы. Автоматические
production claims отключены reconciliation marker, реальные outbox/provider
невозможны. Старые реальные сообщения не re-enqueued.

Обычный full user Bearer/OAuth: MCP userio.diagnostic.create/get/message,
HTTP POST /v1/diagnostic/chats, POST /v1/diagnostic/messages,
GET /v1/diagnostic/chat?conversation_id=... . Create принимает request_id,
optional title/actor_ref; message принимает conversation_id/request_id/body,
optional direction=incoming|outgoing. request_id16–128 alnum_- immutable;
изменённый payload с тем же ID отказывает, повтор возвращает same message.
actor_ref только подпись тестового участника, НЕ права и НЕ его провайдерский ID.
Настоящие permission/role checks делает приложение по своей свежей directory.
Private read_only, messenger-account и recorder scopes не расширяются.

Нормальная внешняя отправка /send и draft approval отказывают для diagnostic:
канал не имеет fallback на Telegram/email/реальный outbox. Приложение сохраняет
ответ как virtual outgoing через diagnostic.message. UserIO не запускает вторую
модель и не выбирает за приложение identity/permissions.

Срез source/tests, не live deployment claim. Настоящая app UI→UserIO→SDK→
UserIO response и проверка реального current-role участника обязательны отдельно.
