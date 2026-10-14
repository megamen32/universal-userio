# Телемост: запись встречи по ссылке

Откройте [Встречи](https://msg.bezrabotnyi.com/telemost), войдя в свой UserIO.
Укажите ссылку `https://telemost.yandex.ru/j/…`, длительность и нажмите
«Подключиться и записывать». Участник «Секретарь · запись встречи» виден другим,
микрофон, камера и публикация медиадорожек выключены. Организатор может потребовать
допуск; ожидание можно отменить. Ошибку/неизвестный результат нельзя исправлять
повторной отправкой того же внешнего действия под новым request_id.

Интерфейс: `userio.meetings.telemost.start/get/stop` и
`POST /v1/meetings/telemost`, `GET /v1/meetings/telemost/{id}`,
`POST /v1/meetings/telemost/{id}/stop`. Персональный readonly остаётся readonly.
MCP start: `url`, уникальный `request_id`, `confirm:true`, `max_seconds`30–7200.
Идентификатор сохраняется до соединения. Запись и расшифровка доступны только
владельцу. API аудио поддерживает Range и ограниченное потоковое чтение.

Транспорт использует схему connection/hello/subscriberSdpOffer из
[WhiteTransport](https://github.com/meanwebuser/whitetransport), отдельно от его
DataTunnel/VPN. Нативный RTC receiver работает на44: не запускается браузер
на100 и не копируются учётные записи Yandex/WhiteTransport. Вход гостевой;
закрытая для гостей встреча может отказать. Прямое опубликованное API360
организаций не является API записи аудио личного аккаунта.

Аудиопотоки сохраняются как mono16kHzPCM WAV с SHA; после окончания автоматически
разбираются существующим Whisper. Имена участников не угадываются по номеру
дорожки. Если SFU смешивает голоса, отдельные говорящие не обещаются. Задержки
включения дорожек отражены в `started_offset`. Неуспех расшифровки не удаляет аудио.

Текущие ограничения: чат встречи пока не подключён; постоянные личные чаты
Яндекс Мессенджера требуют отдельного account-bound соединения. Полная живая
приёмка записи/голоса/транскрипции ещё не закрыта; см. фазу17 GSD.

## Runtime

`deploy/userio-telemost.service`: private Unix socket на44, one meeting,
RAM1/2GiB, swap0, CPU2, tasks128, IOWeight10, максимум2часа/8audio streams/
2GiB per recording, archive admission8GiB +22GiB free disk. Без publisher.
Пакеты aiortc1.14.0, aiohttp3.13.3. Конфигурация100:
`USERIO_TELEMOST_SSH_HOST=192.168.2.5`; SSH через существующий пользовательский
ключ roomhacker. Сам рабочий процесс не принимает произвольные shell-команды.
Данные0700/0600, никаких cookies/secrets в Git. Глобальные модели, аккаунты,
SIP и Telegram transport не меняются.
