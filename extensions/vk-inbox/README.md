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

## Установка в BrowserOS (localhost:9223)

1. Откройте `chrome://extensions`.
2. Включите **Режим разработчика** (Developer mode).
3. Нажмите **Load unpacked** → выберите эту папку.
4. В настройках расширения укажите endpoint UserIO и `route_id`.
5. Откройте VK Web → `https://vk.ru/im` — захват начнётся автоматически.

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
options.html / options.js Настройки endpoint / token / route_id
```

## Безопасность

- VK-куки и токены не покидают профиль Chrome.
- Расширение использует только уже открытую VK-сессию.
- В UserIO отправляется обезличенный envelope `universal.inbox.message.v1`.
- Token UserIO хранится локально в `chrome.storage.local`.
- Сбор данных: задачи приходят только с настроенного endpoint UserIO, результаты уходят только туда же. Расширение никогда не отправляет в UserIO куки/токены сайтов — только тела ответов. `host_permissions: http/https *` нужны, чтобы fetch-рецепт мог обратиться к любому сайту, где пользователь залогинен; это осознанный компромисс открытого расширения.

## Известные ограничения

- Селекторы зависят от VK DOM. Если VK сменит вёрстку — нужна правка `lib/selectors.js`.
- Полная история чата подгружается только при открытии чата в VK Web; мы не дёргаем VK internal API.
- VK может показывать капчу при активной автоматизации; расширение работает в фоне пользователя.
