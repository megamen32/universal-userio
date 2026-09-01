# VK Inbox → универсальный агент сбора данных (universal collect)

Started at 2026-09-01T04:38:37+03:00 (/proc/uptime).

## Запрос пользователя
Расширение должно смотреть на сервер UserIO, забирать оттуда задачи «с такого-то
сайта нужны такие-то данные», исполнять их браузерной сессией пользователя и
отдавать результат на сервер. Отдельное расширение не делать — развить
существующий VK Inbox (решение пользователя, 2026-09-01). История переданных
данных внутри расширения — осознанно не MVP.

## Минимальный путь (3 строки)
- Результат: оператор публикует задачи в JSON-файле на сервере; установленное
  расширение по alarm забирает `GET /v1/collect/tasks`, исполняет fetch-рецепты
  с куками пользователя и постит `universal.collect.result.v1` в
  `POST /v1/collect/results`; оператор читает результаты `GET /v1/collect/results`.
- Канари: реальный Chromium с загруженным расширением → локальный UserIO с
  опубликованной задачей → результат появился в `/v1/collect/results`;
  затем то же самое на проде `universal-userio.service`.
- Срез: `collect.py` (файлы задач/результатов) + 3 роута в `http_api.py` +
  `lib/collect.js` + manifest 0.3.0 (`host_permissions http/https *`) +
  build.sh (mv2 host perms) + pytest + docs + деплой в /opt/universal-userio.

## Не делаем сейчас (discard)
DOM-рецепты (kind: dom), история данных в chrome.storage (просил пользователь —
потом), UI публикации задач в дашборде, ретраи с backoff, push/SSE вместо poll,
изоляция задач по пользователям (задачи общие, результаты тегируются username),
отдельное универсальное расширение.

## Решения
- Тот же extension, те же endpoint/token из lib/userio.js storage. Имя
  расширения → «Universal UserIO Agent», version 0.3.0.
- host_permissions `http://*/*` + `https://*/*` — универсальность вместо
  перечисления сайтов; документируем trust-границу (результаты уходят только
  на настроенный endpoint).
- Рецепты MVP: только `kind: "fetch"` (url/method/headers/body/credentials/
  response), `credentials: include` по умолчанию — в этом смысл (сессия юзера).
- Файлы: `/var/lib/universal-userio/collect-tasks.json` (JSON-список, правится
  оператором без рестарта), `collect-results.jsonl` (append + threading.Lock).
  ENV: `USERIO_COLLECT_TASKS_FILE`, `USERIO_COLLECT_RESULTS_FILE`.
- Деплой: код живёт в /opt/universal-userio (PYTHONPATH=src), systemd unit
  `universal-userio.service`, ReadWritePaths=/var/lib/universal-userio.

## Статус
done (2026-09-01)

Канари пройден на реальной поверхности, дважды:
- Локально: Chromium 152 (branded, `--enable-unsafe-extension-debugging` +
  CDP `Extensions.loadUnpacked`, т.к. `--load-extension` в branded Chrome
  удалён) + локальный UserIO: `collectRun ran=2/2`, оба статуса ok (ipify 200,
  loopback zip 200/20KB), результаты в `GET /v1/collect/results`.
- Прод: то же расширение против боевого `universal-userio.service`
  (127.0.0.1:18093): canary-ipify ok/200, результат записан на проде,
  tasks-файл возвращён в `[]`.

Деплой: collect.py/http_api.py/статические zip скопированы в
/opt/universal-userio, сервис перезапущен, `is-active` = active.

Нюансы, зафиксированные в дороге:
- mv2-alias конвертер в build.sh теперь мержит `host_permissions` в
  `permissions` (иначе mv2 не может делать кросс-доменные fetch).
- websocket к CDP требует `suppress_origin=True` (Chrome 403 на Origin).
- Внешний домен msg.bezrabotnyi.com роутится не на UserIO (там GPTAdmin
  login-страница); UserIO живёт на 127.0.0.1:18093 и публикуется отдельно —
  вне скоупа этого цикла.
