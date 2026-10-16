# Telemost: диаризация и обычная история — 16 октября 2026

Владелец попросил завершить встречу X-manager: запись, собственная сводка
Секретаря и AutoGram. Уточнил: минимум унифицированных tools, обычное чтение
прошлого/файлов; Whisper OpenAI-compatible должен сразу получать diarize.
Группа уже получила собственную сводку Секретаря271, DELIVERED; её не повторять.
Прямое расширение разрешения: «Да, исправь и установи в UserIO» для двух
проверенных source seams и необходимых тестов/штатной установки.

## Владение

src/universal_userio/messenger_max_media.py, telemost/meetings.py,
tests/test_messenger_max_media.py, tests/test_telemost.py, docs/telemost.md,
эта запись и scoped release/canary scripts. Чужие WIP messenger.py,
deploy/telegram-qr-connector/server.mjs, untracked messenger_telegram_mirror.py
и graphify/planning файлы сохраняются. Базу/креды/принятые sends не менять.
Не переподключаться к завершённой встрече; неизвестные операции не повторять.

## Original GSD context

Detector complete; исходные PROJECT/REQUIREMENTS/ROADMAP/STATE и Phase17-01
прочитаны. Бounded debug inline; новая фаза/инициализация не нужны.
Текущий milestone v1.3 не объявляется завершённым.

## Доказательства и план

90с реального WAV встречи af8c369c-4fdd-52c9-ac98-4a2bf4ff7b63 через
собственный endpoint с diarize=true/verbose_json: 15.05с,23segments,
3speakerlabels. Существующий helper не передаёт флаг, хранит только text;
ordinary meeting conversation содержит лишь4status-сообщения.
Патч подготовлен/проверен в X-manager4fc906a; public remote подтверждён.

1. Передавать флаг и сохранять исходные сегменты/таймкоды/анонимные метки.
2. Публиковать исходный текст в ТОТ ЖЕ owner conversation с постоянными IDs.
3. Сохранять scope меток каждого120секундного куска; разные SPEAKER_00 не
   считаются одним человеком между запросами.
4. Focused tests, scoped main commit/push, immutable container release,
   реальная архивная ASR/ordinary history readback без join/send.

## Бюджет

Одна тяжёлая работа одновременно. Тесты: soft2GiB/hard3GiB address space,
CPU4 affinity, один pytest process/BLAS thread1, finite200s emergency для
expected20s, temp2GiB/file ceiling. Runtime backend сохраняет3GiB/CPU4/
swap256MiB/PIDs512, voice/frontend не заменяются. Before build измерить свежую
доступную память и существующий immutable input; не менять чужие процессы.
ASR остаётся на существующем bounded Whisper server88 через его API.

## Результат

20focused tests PASS18.99с на том же native RTC dependency image; новые проверки
подтверждают точные formfields, неизменённые обычные voice requests, сохранность
segments/speakers, owner/source/reconciliation,120с offsets и отдельные scopes.
Тестовый container был bounded3GiB/CPU4/PIDs128/tmp128MiB/networknone и удалён.
Первый запуск отказал из-за отсутствующего pytest в штатном venv; использованы
существующие pure pytest modules readonly, production packages не менялись.

Scoped delivery использует текущий acceptedbackend22f77252 как immutable parent
и заменяет только2publishedfiles. Все прочие Python source hashes, limits,
mounts, frontend/voice containers сверяются без изменений. Это scoped image,
а не заявление о полном совпадении runtime со всем текущим Git tree.
Новые зависимости/секреты/провайдеры не устанавливаются.

Исходник24cd36a3e0f75f33b73690c13e3b20a4c2ee402d опубликован в main и установлен:
image248d722dcf58eb0e27d44b53cea02b7c66ec0f8337aee1de8493cf78cef9d18b.
Backend healthy; текущая наблюдаемая нагрузка405.5MiB/44tasks при3GiB hard.
Изменились ровно2Python source hashes; все прочие installedsource неизменны.
Frontend/voice container IDs сохранены, limits равны прежним, bindmounts
сверены с канонической Compose-конфигурацией. Проверка releasecontroller
сначала отказала на порядке списка Binds после успешного запуска: исправлено
сравнение sorted; независимый readback прошёл БЕЗ повторной установки.
Это ошибка verifier, а не отсутствие установленной правки.

Полная существующая36мин запись проходит установленный _archive:
18последовательных реальных120с ASR запросов с diarize. Перед началом сохранён
original meeting.json (before-diarization-20261016.json), PCM/SHA не меняется.
Remoteaudio seam читает существующий проверенный WAV; join/stop/LLM/send нет.
Используется настоящий UserIOService.receive/SQLiteUserIOStore, не ручной SQL.
Первый текст уже прочитан через штатный native Secretary userio_read как
чатом, так и точным message_id. Полная приёмка PASS:18/18частей,604segments,18scopes,335.69с, WAV2159.699с
с исходнымSHA. Independent owner MCP conversation_get:22messages,18transcripts,
18uniqueIDs, во всех18speakertext. Actual installed Secretary userio_read
потом прочитал каждый из18точных message_id за6.118с; каждый body совпал с
installed archive без обрезки, правильные conversation/source/direction.
No join/LLM/outbound replay. Private receipts: .tmp/20261016-meeting-diarization/.
X-manager native readback: var/20261016-meeting-secretary/installed-meeting-history-readback.json.

GSD acceptance gate применён к внутренней service-интеграции: focusgroup не
добавляет независимого сценария; final installedsource был заморожен перед
архивной ASR и обычным readback. Прямое правило владельца разрешает scoped
publication при чужом WIP, поэтому wholecheckout clean не заявляется и чужие
файлы не блокируют принятую собственную вертикаль. Source tests20PASS;
реальный affectedconsumer18/18PASS. Последующие изменения только taskdocs/scripts,
без изменения установленного productcode или перезапуска runtime.

X-manager запись и private receipts:
/home/roomhacker/agents-projects/exmanager/var/20261016-meeting-secretary/.
