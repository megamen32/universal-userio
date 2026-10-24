# UserIO: три контейнера

## Граница продукта

Frontend, backend и voice — части одного универсального UserIO, а не отдельные
сервисы уведомлений. UserIO обслуживает сообщения, чаты, звонки и встречи,
включая Telegram, Matrix, Телемост и сотовые каналы по их capabilities.
Notice Place — потребитель UserIO: вопросы, уведомления, выбор канала и время
контакта. AgentCall — внутреннее имя голосового моста в UserIO Voice.
Asterisk/GSM2SIP относятся к сотовому адаптеру, не ко всем каналам.
[Единый контракт](COMMUNICATIONS-ARCHITECTURE.md).

Этот раздел не меняет размещение или здоровье работающих процессов. Датированные
квитанции ниже сохраняют историю; актуальные контейнеры, leases и config нужно
сверять перед действием. Не запускайте старый host gateway параллельно voice
из-за одного статуса failed у совместимого oneshot-unit.

## Установка и история поставок

Текущая установка `msg.bezrabotnyi.com` управляется Docker Compose.

| Контейнер | Что выполняет |
|---|---|
| frontend | Раздаёт сайт и передаёт запросы API и авторизации |
| backend | API, MCP, история, получение сообщений, мост Matrix, обработка событий и предложений |
| voice | Голосовой шлюз, синтез речи, запись встреч и очередь расшифровки |

Backend и voice собираются из одного образа приложения с разными командами и
настройками. После точечного обновления API backend использует дополнительный
слой http_api/store поверх принятого b81ef6c; voice сохранён без перезапуска.
Frontend использует образ nginx. Контейнеры можно перезапускать
отдельно: это проверено остановкой voice при работающем API и сохранённых17аккаунтах.
Связанные процессы внутри контейнера сохраняют прежние UID, окружение,
курсоры и журналы; supervisor проверяет каждый процесс и завершает только своих детей.

## Библиотека и сервер

`userio_adapter_sdk` — библиотека Python без обязательных внешних зависимостей.
Её можно импортировать в другое приложение без Docker, веб-сервера, systemd
и FastAgent. Конкретный адаптер подключает только необходимые ему зависимости.

```python
from userio_adapter_sdk import Omnichannel
channels = Omnichannel()
# channels.register(ваш_адаптер, account_id="personal")
```

См. [Adapter SDK](adapter-sdk.md). Готовый сервер UserIO добавляет авторизацию,
общий inbox, постоянный приём событий, веб и MCP. Его можно использовать по
API из приложения на любом языке. Три контейнера относятся к этому серверу.

## Данные и внешние платформы

Существующие каталоги `/var/lib/universal-userio`, Matrix-моста, dispatcher
и записей звонков остаются постоянными томами. Сохранены17учётных записей,
241подтверждённая отправка, ключи, история, сессии и курсоры. База не откатывается
поверх новых данных; принятые и неопределённые действия не отправляются заново.

Настройки работающих процессов сохранены в root-only `/etc/userio-containers`:
`processes.json`, `voice.json` и `compose.host.json`. Значения секретов не входят
в Git, образы или контекст сборки. Установленные файлы ключей подключаются
по прежним путям. PATH включает системные утилиты, native thread pools ограничены.

Телефон/Asterisk, SMS gateway, Matrix homeserver, NoticePlace и AgentHerder
остаются общими внешними платформами. Сохранены короткие обращения к44 для
чтения Яндекса и старого архива звонков. Постоянный RTC recorder с44 перенесён
в voice вместе с7terminal-записями; повторного вступления во встречи нет.

Исходный host `s21-lease` не изменён. Прежняя `agentcall-gatewayd.service` теперь
короткий oneshot-адаптер проверки/выдачи/возврата lease, без постоянно работающего
процесса. Он проверяется настоящим native systemd InvocationID и исходным guard.
Запрос потребляется один раз, имеет конечное время ожидания и не повторяется
при старом ручном restart. Занятое устройство остаётся занятым.
Расшифровка выполняется внутри voice по одному заданию; UNKNOWN не перезапускается.
Неиспользуемый Windows TTS tunnel выведен из автозапуска.

## Эксплуатация и откат

Канонический checkout остаётся на main. `scripts/container_release.py context`
собирает только опубликованные Git-объекты и явно зафиксированные установленные
зависимости. Чужой WIP исключён. Сборка выполняется в конечном resource budget.

```bash
sudo docker compose -f compose.yaml -f /etc/userio-containers/compose.host.json ps
```

Новые версии доставляются образами через эту конфигурацию Compose после проверки
отсутствия активных звонков. Прежние `runtime_release.py install/activate/restore`
и `activate-userio-voice.py` не являются маршрутом этой контейнерной установки.
`prepare`, `prepare_voice`, `cutover`, `finalize` — этапы уже выполненного переноса;
повторно запускать cutover нельзя.

Фактическое состояние на 16.10.2026: живой `userio-backend` создан через
`docker run` и **не** несёт compose-меток, поэтому `docker compose up` для него
падает с конфликтом имени контейнера. Точечное обновление backend выполняется
тонким образом поверх принятого и пересозданием контейнера с той же
конфигурацией (bind-набор, память, host-сеть, ulimit, log-opt) и автоматическим
возвратом предыдущего контейнера при любой неудаче; рабочие примеры:
`.agents/tasks/20261016-meeting-release.py` (сборка) и
`.agents/tasks/20261016-personal-send-activate.py` (активация). Compose-конфиг
обновляется в том же атомарном шаге, иначе он расходится с живым образом.

Откат текущего переноса:

```bash
sudo python3 scripts/container_release.py rollback
```

Он останавливает только собственные контейнеры, возвращает прежний nginx route,
native units и RTC worker. Их исходники, конфигурация и состояние сохранены;
старые SQLite backups предназначены для восстановления, а не для перезаписи
новых receipts. Действующие URL, MCP/OAuth и cookie-сессии сохранены.

## Конечные бюджеты

| Процесс | RAM reserve / hard | Swap | CPU | Tasks | Temp | Логи |
|---|---|---|---|---|---|---|
| backend | 2 / 3GiB | 256MiB | 4 | 512 | 144MiB | 30MiB |
| voice | 2 / 5GiB | 256MiB | 4 | 512 | 144MiB | 30MiB |
| frontend | 64 / 128MiB | 0 | 0.5 | 32 | 17MiB | 10MiB |
| Native lease oneshot | 128 / 128MiB | 0 | 1 | 32 | Два малых JSON | journald |

Суммарный локальный hard8.25GiB меньше прежних9.125GiB плюс неограниченный shim.
Удалённые2GiB recorder не заимствованы как бюджет server-100. Измеренная нагрузка
после переноса около770MiB. GPU не используется. Сохраняются FastAgent CLI0.10.43
и отдельный MCP Python0.10.42; Silero использует прежний кэш модели.

Временный builder:1GiB RAM,CPU2,pids256,swap0,parallelism1,cache6GiB,
expected480s/emergency4800s. Host source-job reserve20GiB не снижался:
следующая сборка получила defer75, поэтому использован уже проверенный общий
app-образ. Builder и свои промежуточные образы удалены. На контекст/образы/кэш
выделялось до12GiB при351GiB свободного диска и резерве20GiB.

## Подтверждённый результат и граница проверки

Реальный headed browser44 сохранил вход через переключение и перезапуски;
3существующих PDF и файл/картинка из Избранного скачаны с прежними SHA256.
MCP Bearer и OAuth прочитали17тех же аккаунтов. Проверены native чтение Telegram,
MAX,4почтовых ящиков, свежий Яндекс, старый архив и backend→voice audio.
Проверены WAV-синтез и настоящий отказ phone guard при занятой аренде.
Новых реальных звонков и повторных отправок в ходе переноса не было.

WhatsApp уже до переноса имел пустой файл авторизации и QR-ошибки; он сохраняет
это состояние. Для его подключения нужен обычный QR-вход владельца.
Подробные доказательства: [GSD verification](../.planning/quick/261015-p4h-consolidate-userio-backend-and-frontend-/261015-p4h-VERIFICATION.md).

## Новый рабочий inbox —15.10.2026
Компактная панель,выбор нескольких аккаунтов,папки и мобильная версия выложены
на сам сайт; [руководство](inbox-navigation.md). Frontend c081aa9:
`sha256:e059cf688a09d432a1e7b0951031669189b97fec6af0c86f1b3857ee3e02c69c`.
Backend API/store fc72254 поверх b81ef6c:
`sha256:22f772523ff4a8e545a036d0e8930f896b4f3c2465c1d61dc583e343c06e949b`.
Voice остаётся на прежнем app b81ef6c,start16:35:30Z. Новый API добавляет только
readonly conversation_ids filter,схема БД и прежние env/volumes не менялись.
Все17account identities подтверждены действующим MCP и совпали с baseline.
Для backend/frontend сохранены предыдущие локальные теги
`universal-userio-app:before-folders-20261015` и
`universal-userio-frontend:before-inbox-20261015`.

## Поиск истории —17.10.2026

Поиск обслуживает backend92f8ffc, сайт — отдельный frontend8e76b94; наличие
нового static в backend не обновляет nginx frontend. Оба слоя берут только
опубликованные Git objects и сохраняют runtime-конфигурацию. Voice не менялся.
Точные helpers и откатные контейнеры — в журнале задачи поиска; настройки
Qdrant/embeddings и секреты остаются в действующем защищённом хранилище.

## Плановое обновление только backend

Одноразовый `cutover` не предназначен для обновлений. Используйте
`replace-backend` с опубликованной ревизией и точным ID текущего контейнера:

```bash
sudo python3 scripts/container_release.py replace-backend \
  --image universal-userio:<published-tag> --revision <full-published-commit> \
  --expected-container <current-full-container-id> \
  --receipt-directory /var/backups/universal-userio/<unique-release>
```

Команда проверяет metadata и source hashes образа, отсутствие звонков/встреч,
здоровье supervisor и API, затем сохраняет закрытые snapshots Config/HostConfig,
окружения, mounts и исходные bytes Compose. Она заменяет только канонический
`userio-backend`; frontend и voice сохраняются. Единственная разрешённая
нормализация HostConfig — `OomKillDisable: null ↔ false`. Остальные Config/Env
и HostConfig сравниваются точно. Обновление Compose атомарное.

При ошибке команда восстанавливает backend из сохранённого образа и
конфигурации, возвращает bytes Compose и проверяет supervisor/API. Базы и сессии
не откатываются. Каталог содержит intent, исходный snapshot и конечную receipt;
повтор успешного вызова возвращает ту же receipt, незавершённый или неуспешный
вызов требует разбора сохранённого состояния и не повторяет замену автоматически.
Команда блокирует параллельный запуск этой же процедуры и проверяет ID живого
контейнера непосредственно перед остановкой. Старый образ сохраняется локально;
новое имя временного контейнера не создаётся.

## Архив текущего UserIO Voice без перезапуска

Перед отдельной приёмкой можно сохранить текущее состояние работающего Voice:

```bash
sudo python3 scripts/container_release.py archive-voice \
  --receipt-directory /var/backups/universal-userio/voice-<unique-release>
```

Команда только читает контейнер, образ, подключённые файлы конфигурации и
смонтированные исходники. Источники читаются из `/proc/<container-pid>/root/<mount-destination>`:
это содержимое, которое видит работающий контейнер. Путь остаётся лексическим;
его нельзя предварительно раскрывать через `realpath`, иначе можно попасть в
текущий host-файл вместо старого inode, закреплённого bind mount. Если bridge
является symlink, архиватор записывает его текст и проходит каждый компонент
через открытые directory descriptors с `O_NOFOLLOW`; абсолютная цель
интерпретируется от корня контейнера, а `..`, сломанные и меняющиеся ссылки
отклоняются. Для целевого файла записываются device/inode/hash, для корней
каталогов — device/inode. Перед публикацией архив повторно сверяет PID и mount
namespace контейнера, текст ссылки, inode и хэши bridge/SDK/config, а также
Docker Config/HostConfig/Mounts; Mounts сравниваются как отсортированный набор
полных записей, поэтому порядок ответа Docker не считается изменением, но любое
изменение поля, добавление или удаление mount отклоняется. При расхождении
временный архив удаляется и команда завершается ошибкой.

Команда не останавливает контейнер, не публикует образ и не делает звонков.
Требуются живой `userio-voice`, immutable registry digest, revision label,
доступные bridge/SDK источники и сохранённый остановленный контейнер
`userio-voice-prev-*`; при отсутствии любого из них архив не создаётся.

Архивный каталог создаётся с правами `root:root 0700`, все файлы —
`root:root 0600`. Raw Docker inspect и подключённые JSON-конфиги могут содержать секреты;
они остаются только в этом локальном каталоге и никогда не попадают в Git или
stdout. `manifest.json` содержит redacted fingerprint, image/source identity,
SDK и bridge хэши, rollback-ссылки и SHA-256 файлов. Архив публикуется атомарно
без перезаписи существующего назначения; `SHA256SUMS` проверяется повторным
чтением перед публикацией. Блокировка сериализует только запуски этой команды.
Внешние писатели не блокируются: повторная сверка обнаруживает сохранённые
изменения и смену inode во время захвата, но не обещает атомарный снимок при
внешнем изменении и полном откате байтов между двумя проверками. Эта операция
фиксирует исходное состояние для восстановления; сама по себе она не является
проверкой поведения звонков или приёмкой релиза.

## Portable UserIO Voice (versioned image)

Это режим существующего UserIO Voice, а не новый ExManager Voice. Notice Place
сохраняет политику контакта; ExManager Secretary SDK остаётся внешним API.
Один target `portable-voice` содержит canonical engine, MatrixRTC Python dependencies,
FastAgent и опубликованный подписанный Secretary adapter. STT/TTS — существующие
управляемые endpoint-сервисы: локальный Silero/Torch не скачивается при старте.
В image нет аккаунтов, токенов, identity map, телефонных привязок или записей.

На чистом Linux/amd64 нужны Docker Engine, обычный `docker login ghcr.io` для
закрытого registry и root-owned защищённый каталог `/etc/userio-voice`.
`environment.json` внутри него имеет права0600 и содержит стандартные
`AGENTCALL_*`/`EXMANAGER_VOICE_*` настройки; защищённые file references должны
указывать внутрь `/etc/userio-voice`. Ключи/identity map размещаются там же с0600.
Постоянные записи и FastAgent state хранятся в `/var/lib/userio-voice`; при update
не удаляются. Этот режим не переносит БД, OmniRoute, Asterisk или S21.

После подготовки конфигурации установка и обновление standby — одна команда
(IMAGE — точный `ghcr.io/megamen32/universal-userio-voice@sha256:...` из receipt):

```bash
sudo docker run --rm --pull=always --network none \
  -v /var/run/docker.sock:/var/run/docker.sock --entrypoint python "$IMAGE" \
  /opt/universal-userio/scripts/container_release.py install-voice \
  --image "$IMAGE" --role standby --name userio-voice \
  --config-dir /etc/userio-voice --data-dir /var/lib/userio-voice --port 19095
```

Installer из того же immutable image обращается к существующему Docker Engine;
Git/checkout/Python/pip на целевом хосте не нужны. Он не печатает конфигурацию.
Standby стартует тот же engine, без ingress/poller/caller, с loopback19095–19104,
собственными Unix sockets и без PBX control mount. Ни9092, ни действующиеSTT/TTS
порты он не занимает. Health проверяет pinned modules/adapter и Matrix imports,
появление control/event sockets, без звонка или LLM-запроса. Окно75секунд;
при неудаче прежний контейнер возвращается автоматически без восстановления БД.
Ровно один `-previous` сохраняется для отката. При следующем update только
собственный остановленный rollback временно архивируется; после health нового
контейнера он удаляется без удаления volumes. Работающий или чужой rollback
не трогается. При неудаче оба прежних контейнера возвращаются на свои места.

Calls-primary использует тот же digest, `--role calls-primary --port 9092`.
Перед заменой работающего primary его единственный runtime owner сверяет все
accepted/UNKNOWN receipts и idle, затем останавливает точный компонент; installer
не останавливает работающий primary самостоятельно и не заменяет чужой native
release path. На88 переход требует Root/native join. Primary config требует
явныеSTT/TTS URL и `AGENTCALL_LLM_PROVIDER=openai-compatible` (никакого warm LLM
при старте). Backend UserIO продолжает запускать штатные account-scoped workers;
standby не становится их получателем. Автоматического failover/phonefallback нет.

Сборка: `container_release.py context --portable-voice --output <context.tar>`
берёт только опубликованный release и exact published adapter из manifest. Затем существующий
shared builder `docker buildx build --builder k3s-ci-shared --target portable-voice
--build-arg USERIO_REVISION=<sha> --platform linux/amd64 --push -t <immutabletag> <build-context-dir>`
читает распакованный release tar (это buildartifact без Git, не новый checkout). Digest и module manifest — identity релиза. На всех
узлах44→100→88last используется этот digest; повторных сборок/pipinstall нет.
Existing unattended updater остаётся paused до доставки такого release; его
старый source-build режим не должен включаться для portable image. Порядок,
idlejoin и конечная реальная канарейка принадлежат текущей rolling-release задаче.

Telegram native media пока не объявлен portablecapability: проверенный wheelpin
и его workerinterpreter для текущего88 не установлены поmetadata; старый3.0.3
не подмешивается в этот image. Matrix/phone engine и SDKadapter от этого не зависят.

Portable target устанавливает только requirements-portable-voice.txt (Matrix,
FastAgent и NumPy) с frozen constraints из прежнего requirements.txt. Он не
наследует backend/Telemost/browser/Torch слои. Результат pip freeze находится
в image `/opt/userio-voice-dependencies.txt`; исходные dependencySHA входят
в portable-release.json. Это ограничивает рабочий набор общей сборки без
повышения лимитов Kubernetes.

Проверка на44 выявила timeout старого installer; portable create теперь использует
Docker init (как существующий Compose), timeout API40секунд при stop20секунд и
не повторяет stop уже остановленного компонента. Тонкий target
`portable-voice-controller` переиспользует проверенный runtime digest4c280012,
заменяет только controller и опубликованный adapter5de41fd7; pip/apt не запускаются.

Calls-primary также публикует существующий typed control/event IPC через
`/run/agentcall:/run/agentcall` для штатного UserIO backend; standby оставляет
эти sockets в private tmpfs. Это runtime IPC, не code mount. Native owner
останавливает старый gateway до primary-перехода, чтобы не было двух владельцев
одних sockets/9092. Входящий AudioSocket и Matrix control используют тот же image.

## Проверенная portable версия

Release: [voice-415a101.json](../deploy/containers/releases/voice-415a101.json).
Образ закреплён digest, исходник415a101 и adapter5de41fd7. На44 доказаны
чистый standby startup, exacthashes11modules+adapter, повтор без перезапуска и
откат после неверной QAконфигурации с сохранением исходного containerID/данных.
Это приёмка поставки; реального разговора эта проверка не выполняла.

После обычного доступа Docker к private registry и подготовки root-owned0600
`/etc/userio-voice/environment.json` на выбранном хосте:

```bash
docker run --rm --pull=always --network none \
  -v /var/run/docker.sock:/var/run/docker.sock --entrypoint python \
  ghcr.io/megamen32/universal-userio-voice@sha256:f369fed158e9f91d2b636405cef5cfb62c04ed543f24993d928380ba15b12802 \
  /opt/universal-userio/scripts/container_release.py install-voice \
  --image ghcr.io/megamen32/universal-userio-voice@sha256:f369fed158e9f91d2b636405cef5cfb62c04ed543f24993d928380ba15b12802 \
  --role standby --name userio-voice --port 19095 \
  --config-dir /etc/userio-voice --data-dir /var/lib/userio-voice
```

Docker CLI и registry login должны принадлежать одной вызывающей учётной записи.
Для remote Docker operator можно использовать существующий Docker SSH context;
cookies/ключи в команду не копируются. Для primary используются тот же digest,
`--role calls-primary --port 9092` и защищённая рабочая конфигурация; старый
gateway останавливает его runtime owner после сверки idle/неопределённых
квитанций. На88 это отдельный согласованный переход, не часть standbyтеста.

Existing updaters выбирают image-mode только при root-owned
`/etc/userio-voice/release.json` с полями image,role,container_name,config_dir,
data_dir,port (никаких секретов). На всех узлах image одинаков, role различается.
Их hold/timer не включаются установщиком; legacy source-build mode не нужен
этому release. Ни SDK, ни БД, ни SIPpin/HA не обновляются этой командой.

Готовые точные public payload для updater находятся в release artifact:
`updater_release_templates.standby` и `.calls-primary`; это отдельные6полей,
а не весь расширенный manifest. Они входят в обычную protectedconfigпоставку.
QAконтейнеры/каталоги на44 после приёмки удалены; image остаётся в cache и registry.

### Host Asterisk group for incoming calls

Set `AGENTCALL_INCOMING_SOCKET_GID` as a string in protected
`/etc/userio-voice/environment.json` when the host Asterisk numeric group differs
from the image's legacy `asterisk` group. For the verified88 deployment it is
`"147"`. Other hosts retain their own verified mapping; do not change all image
or host groups to147. Only positive canonical decimal IDs1..2147483647 are
accepted; whitespace, empty, zero and signed values fail before listeners start.
Without the setting the existing named `asterisk` group remains the default
(163 in the portable image). `incoming.sock` stays0660, never world writable.
The existing health check now verifies its actual GID and mode, including a host
numeric override. This is permission readiness, not a phone business acceptance.
