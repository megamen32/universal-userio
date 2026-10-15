# UserIO — повторный отказ hosted-плагина

Исходное прямое поручение пользователя: отдельный исполнитель чинит подключение
UserIO в его canonical проекте, не GPTAdmin/Airlock. Делегированный root повтор
15 октября: hosted accounts_list возвращает UNAVAILABLE, прямой MCP успешен.
Границы исполнителя userio_connector_repair: минимальная диагностика/fix только
реальной регистрации/маршрута UserIO; без отправок/OTP/новых аккаунтов, сохранение
чужого WIP. В этой итерации принадлежат только этот task record и отчёт проверки.

Текущий продуктовый контекст сохраняется: `.planning/STATE.md`,
`.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md` и
`.planning/phases/17-telemost-chats-and-quiet-meeting-recording/17-01-PLAN.md`.
Проверка независима от принятой фазы 17 и не меняет её историю или объём.

## Результат и оставшаяся граница

В сессии исполнителя два реальных hosted accounts_list и два прямых MCP чтения
успешны: 17 аккаунтов. TLS, protected-resource/auth-server discovery работают;
служба active без перезапуска. Дефект продукта/конфигурации не воспроизведён,
точная причина root-отказа неизвестна. Backend/auth/ingress не менялись.
Нужен собственный повтор root hosted-вызова; если отказ сохраняется, точный
request ID/UTC/URL для внешнего connector trace. Прямой MCP остаётся проверенным
рабочим маршрутом. Доказательства и граница:
`docs/verification/20261015-codex-plugin-connection.md`.
