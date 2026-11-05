# Universal UserIO Agent extension (v0.3)

Полнофункциональный Chrome MV3-адаптер VK Web для Universal UserIO **и**
универсальный агент сбора данных с сайтов: расширение опрашивает сервер
UserIO, берёт оттуда задачи «с такого-то сайта нужны такие-то данные»,
исполняет их реальной браузерной сессией и возвращает результат на сервер.

## Что умеет

- Забирает **все чаты** в реальном времени через MutationObserver над `me_convo_list` и плавающей панелью `.FCThumb`.
- Забирает **сообщения** открытого чата (DOM `data-msgid` / `[class*=Message]`) и индексирует их в локальном IndexedDB.
- **Ищет** по всем сообщениям (substring match по телу и идентификатору собеседника).
- **Отправляет** сообщения через активную VK Web-сессию: открывает нужный чат, вставляет текст в поле ввода, кликает Send.
- Пересылает каждое захваченное сообщение в UserIO `POST /v1/messages` (route_id `vk-browser`) — UserIO остаётся каноническим стором.
- **Собирает данные с сайтов** (v0.3): раз в минуту забирает `GET /v1/collect/tasks`, исполняет fetch-рецепты с куками пользователя (`credentials: include` по умолчанию) и постит `universal.collect.result.v1` в `POST /v1/collect/results`. Задачи публикует оператор в JSON-файле на сервере (`/var/lib/universal-userio/collect-tasks.json`); пример — `collect-tasks.example.json`.

## Установка и запуск через Agent Browser

1. В обычном Chrome откройте `chrome://extensions`, включите **Developer mode** и загрузите распакованную папку `extensions/vk-inbox/` через **Load unpacked**.
2. Убедитесь, что это профиль Chrome, где уже выполнен вход в VK Web.
3. Используйте Agent Browser с тем же профилем:

   ```sh
   agent-browser --session vk-userio --profile Default open https://vk.ru/im
   agent-browser --session vk-userio snapshot -i
   ```

4. В настройках проверьте endpoint UserIO (по умолчанию `https://msg.bezrabotnyi.com`) и `route_id`, затем нажмите **Войти в UserIO** и подтвердите нужную учётную запись. Для своего сервера замените endpoint. После этого откройте VK Web — сбор начнётся автоматически.

## Подключение аккаунтов

В настройках расширения сначала задайте адрес UserIO. Нажмите **Войти в UserIO**:
откроется штатная страница UserIO. Войдите в нужную учётную запись и подтвердите
доступ расширения; после успешного OAuth входа расширение проверит доступ чтением
вашего inbox. OAuth access/refresh tokens хранятся только в хранилище этого
профиля Chrome. Общий ZIP не содержит пользовательских токенов.

Аккаунт ChatGPT выбирается отдельно кнопкой **Проверить аккаунт ChatGPT**. До
кнопки подтверждения cookies не отправляются; после выбора расширение сверяет,
что сессия не сменилась, и только затем передаёт её в подключённый UserIO.
Удалённая команда не может инициировать эту передачу.

## Архитектура

```
manifest.json             MV3 v0.3.0, service_worker, host: http/https *
background.js             SW: IndexedDB, UserIO bridge, очередь forward, collect-старт
lib/db.js                 IDB schema: chats, messages, drafts
lib/userio.js             HTTP-обёртки /v1/messages, /v1/inbox/seen, универсальный call()
lib/collect.js            Агент сбора: poll задач, fetch-рецепты, пост результатов
lib/selectors.js          Селекторы с fallback'ами (redesign 2026 + старый IM)
content/chat-list.js      Observer списка чатов
content/chat-view.js      Observer открытого чата + sendText-мост
popup.html / popup.js     UI: чаты, поиск, отправка, статус сбора + кнопка ↻
options.html / options.js Настройки endpoint / route_id / account connection
```

## Безопасность

- VK-куки и токены остаются в профиле Chrome. Cookies ChatGPT передаются только после отдельного выбора и подтверждения.
- Расширение использует только уже открытую VK-сессию.
- В UserIO отправляется обезличенный envelope `universal.inbox.message.v1`.
- UserIO OAuth access/refresh tokens хранятся локально в `chrome.storage.local`.
- Сбор данных: задачи приходят только с настроенного endpoint UserIO, результаты уходят только туда же. `host_permissions: http/https *` нужны, чтобы fetch-рецепт мог обратиться к сайтам, где пользователь залогинен.

## Известные ограничения

- Селекторы зависят от VK DOM. Если VK сменит вёрстку — нужна правка `lib/selectors.js`.
- Полная история чата подгружается только при открытии чата в VK Web; мы не дёргаем VK internal API.
- VK может показывать капчу при активной автоматизации; расширение работает в фоне пользователя.
