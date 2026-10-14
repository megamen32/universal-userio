# Подключение плагина UserIO в Codex — 14 октября 2026

Статус на 20:42 UTC: реальный плагин доступен; исходный одиночный сетевой
отказ не воспроизведён, его причина не установлена. Код, авторизация,
конфигурация ingress и службы не изменялись, перезапусков исполнителем не было.

## Исходный отказ и принятая граница

Корневая сессия получила на `userio_accounts_list` hosted-плагина:
`UNAVAILABLE`, `McpServerError: Connection failed`, `network_error`, endpoint
`https://msg.bezrabotnyi.com/mcp`. Прямой MCP вскоре работал.
Пользователь затем явно вызвал @UserIO и поручил отдельному исполнителю
проверить именно этот путь в canonical UserIO.

Canonical source `/home/roomhacker/agents-projects/universal-userio` на server100,
ветка main; runtime `/opt/universal-userio`. Привязка подтверждена
ServersAdministartion inventory/sites/airlock и UserIO runtime reference.
Существующие изменения Telegram connector/messenger и graphify сохранены.

## Реальное принятие потребителем

Целевая интеграционная проверка, ожидаемое время каждого MCP-вызова до60с,
аварийный предел600с; все вызовы завершились. Использован существующий плагин
`mcp__codex_apps__userio_*`, без подмены транспортом или моками.

| Действие | Фактический результат |
| --- | --- |
| accounts_list, первый независимый вызов | ok=true;16 аккаунтов |
| accounts_list, повторный независимый вызов | ok=true;16 аккаунтов |
| Gmail/SMS в обоих ответах | enabled;read/reply |
| inbox_list_new, channel=mail, limit=1 | ok=true;1 сообщение |
| inbox_list_new, channel=sms, limit=1 | ok=true;1 сообщение |

Содержимое почты/SMS, токены и credential references не сохранялись в этом
отчёте. Отправок, изменения аккаунтов, read ACK, повторной авторизации не было.
Это подтверждает подключение и чтение; отправка через провайдер не проверялась.

Прямые namespaces UserIO/AgentMemory/GrepMesh во время проверки исчезали из
live toolset и возвращали `tool ... is not available to the model`. Такой ответ
означает недоступный инструмент в текущем harness, а не доказанный отказ UserIO.
AgentMemory затем вызван штатно через существующий GPTAdmin target с точной
схемой и scope `universal-userio`; отдельный fallback не создавался.

## Ingress и runtime

- Реальный public GET `/mcp` без bearer вернул401, TLS verify0, total0.078с.
- Служба universal-userio active;NRestarts0,MemoryCurrent около226MiB при
  MemoryMax512MiB,6tasks. Новый daemon/build/browser не запускался.
- Nginx `/mcp` идёт на127.0.0.1:18093 с bearer/OAuth; browser SSO здесь не нужен.
- В текущем error.log только два отказа соединения с UserIO `/mcp` в12:42:10
  местного времени, существенно раньше проверяемого инцидента. В текущем окне
  воспроизведения таких строк нет. Это не исключает ошибку вне nginx.
- Общий access.log содержит502 для `/mcp` других/неидентифицированных vhost:
  формат не содержит hostname. Эти строки не приписываются UserIO без связи
  по конкретному запросу. Нельзя чинить здоровый сервис на этом основании.

## Оставшееся неизвестное и следующий шаг

Причина первичного единичного network_error остаётся неустановленной.
Регистрации могут использовать уже проверенные mail/SMS tools плагина.
При повторении сохранить точное UTC-время и operation/request ID ошибки
hosted connector, затем сопоставить с ingress error/access и platform trace.
Если запрос до UserIO не дошёл, нужен trace платформы connector; создавать
новые аккаунты/ключи, переподключать OAuth или перезапускать backend без этого
не требуется. Платформенный support-ticket и внешние сообщения не отправлялись.

Отчёт фиксирует восстановившийся реальный consumer path и неизвестную причину,
а не объявляет несуществующий программный fix.
