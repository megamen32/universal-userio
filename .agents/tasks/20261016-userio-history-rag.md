# UserIO: единый поиск по всей доступной истории, текст + смысл

## Прямое поручение и исполнитель

Владелец16Oct после принятого X-manager memory RAG попросил: «Сделай, чтобы
он мог искать по всей доступной ему истории рабочих переписок в UserIO — и
по тексту, и по смыслу, со ссылкой на исходное сообщение». Уточнил продукт:
реализовать в UserIO, необязательные настройки своего Qdrant, после подключения
доступен semantic search. Продолжает уже назначенная им UserIO OpenCode-сессия
ses_eb9f8ae23ffeEXFOSx7C1Qb2td, точный minimax-coding-plan/MiniMax-M3.1-Flash-Preview.
Никакого fallback/OmniRoute. Это новое прямое продуктовое поручение; прежний
ремонт личной отправки принят с отдельным human re-login blocker и не повторяется.

## Владение и полномочия

Ты владеешь только /home/roomhacker/agents-projects/universal-userio (main), его
исходниками, документацией, тестами, установкой и реальной UserIO приёмкой.
Root владеет X-manager интеграцией. Ты не один в коде: сохраняй чужой WIP,
не откатывай/stash/reset и не включай чужие paths в commit/image. Нужен ранний
компактный контракт уже существующего/расширяемого search MCP для root, чтобы
он подключил X-manager параллельно. Не добавляй второй agent loop/новую БД сообщений.
Разрешены нужные scoped commit/push main, обычная установка с ограниченным
простоем, конфигурация существующих Qwen/Qdrant для данного owner в собственной
коллекции. Не менять чужие коллекции, лимиты соседей, Airlock/core/model defaults.
Прочти применимые AGENTS/AgentMemory/GSD; архив этого поручения в своём проекте.

## Минимальный полный продукт

- Расширить существующий текстовый поиск и прежний MCP/API, не плодить tools.
  Поиск по сообщениям всей уже сохранённой доступной истории, без прежнего
  скрытого50k scan cut-off и без группировки, теряющей нужные message hits.
- Сохранить exact-first контактный поиск/пагинацию/fuzzy UX. Web-поиск и agents
  используют общее ядро и одинаковые актуальные права. Вернуть источник,
  аккаунт, чат, автора, время, message ID, snippet и работающую ссылку на точное
  исходное сообщение (в UserIO, provider link где фактически поддержан).
- В существующих настройках — opt-in Qdrant URL/key/collection + совместимый
  embeddings endpoint/model/key, при наличии existing provider переиспользовать.
  Без конфигурации приложение полноценно ищет текстом. Ключи не выдавать в UI,
  MCP, logs или evidence; использовать существующее хранение secrets/настроек.
- Qdrant — производный индекс текста/имеющихся расшифровок, не все DB-таблицы
  и не аудио/видео blobs. Scoped owner/account/conversation metadata, текущая
  авторизация до поиска и повторное чтение актуального message body/hash перед
  выдачей. Deleted/edited/revoked и чужие owners не протекают в результаты.
  Не расширять read_only connector, не индексировать других owners под одним.
- Bounded resumable initial backfill всей допустимой истории, checkpoint,
  incremental ingress/edit/delete и безопасная повторная производная запись.
  Не обещать всю историю при partial coverage; UI/API показывают readiness,
  indexed/pending/error и явный text_only fallback при недоступности vector service.
- Измерить количество сообщений/текст/ожидаемые chunks и фактическую скорость,
  RAM/disk; задать конечный budget по текущим ресурсам. Использовать existing
  Qwen3 embedding2048 gateway88:11440 и Qdrant88:6333 c existing TLS/CA,
  scoped token только своей collection; не копировать plaintext секреты в файлы.
  Existing Exmanager collection exmanager_memory_qwen3_2048_v1 не трогать.

## Результат и проверка

Source tests: exact text and paraphrase-only semantic, edits/deletes, owner and
scope revocation, restart/resume, outage truthful fallback, message deep-link.
Реальный installed UserIO API/MCP и owned managed browser44/88: настройки
save/reload/status, смысловой поиск настоящего сообщения и переход к нему без
внешних отправок, ACK/read changes или тестовых обязанностей реальным людям.
Не считать synthetic fixture/schema за production acceptance. Root отдельно
доказывает обычный private Manager answer с источником через разрешённый read API.
Документация короткая, связная: фича/настройки/границы/как работает индекс,
подробные инварианты рядом с кодом. Сохрани разбор, source/runtime/consumer
доказательства. Ответ только: нужное решение/новая ошибка/контракт/итог, без ACK.

## Не повторять старое

draft_3e0e247a3d774795b50f9f9a30ba686a остаётся SENDING/UNKNOWN нетронутым.
Никаких отправок/входа вместо человека/правок Telegram сессий и вручную stateDB.
Forenergo184/AutoGram271/встречи уже приняты, не переигрывать. Read-only history
не зависит от QR re-login для отправки. Сохрани установленные voice/Telemost/auth
repair и чужие server.mjs/messenger.py/mirror WIP. Один тяжёлый workload в
конечном project budget, резерв SSH/БД/соседям, никаких обходов admission guards.

## Передача оставшегося исполнения17Oct
MiniMax twice returned provider.quota429/2056 (Token Plan exhausted); no model
fallback. Direct owner reply: «Заверши здесь, текущим Codex». Codex01a1443d now
owns remaining semantic_index.py/tests, App.tsx/static, focused checks, install
and managed browser44 proof plus docs. Existing source167124c/cea876f/bf69d6b
already installed, foreign connector/messenger/mirror/graphify/phase17 untouched.
Current route: same task original GSD quick; query-only Qwen prep (no reindex),
old source deep-link wait/same-chat navigation/poll stability, scoped publish,
thin same-config install, real queries + browser + natural Manager answer.
Source tests budget768MiB RAM/swap256MiB/CPU2/tasks128/temp256MiB; build512MiB
RAM/swap256MiB/CPU2/tasks128. One at a time. Fresh host available33GiB, reserve20GiB.
Expected tests20/max200s; build15/max150s. Browser44 same managed AgentBrowser
route adopted15Oct:1/2GiB RAM,swap0,CPU2,tasks384,temp128MiB,IO10,wall600s.

Current original GSD quick: .planning/quick/261017-0oq-finish-unified-userio-history-search-own/261017-0oq-PLAN.md.
28history checks12.41s PASS; bounded UI build7.04s/peak310104KiB PASS.
Published7e87827 installed backend31.879s/same config/4exact files; first frontend
attempt rolled back automatically because override had no frontend key. Fixed
setdefault, installed separate frontend3cc0e8c with exact Docker HostConfig.
Do not infer frontend from backend static files: live site uses separate nginx.
Managed headed44 original source188214466:1964912 (2004newer) renders201rows and
exact source at top130px; same-chat1974941 also at130px. Original auto_read=true
was temporarily false for these reads then restored; no outgoing sends.
Natural Manager final14a9fb12 SUCCESS38.884s,2search+2originalread: short correct
answer with budget parameters/source quote and exact link. Parent retains private
receipts. Plain report and IKIGAI semantic targets rank1 each (no supplied prefix).
Settings UI exposed blank saved fields despite effective deployment defaults;
fixed safe effective view and made direct per-user resolution match worker's
no-owner-secret-inheritance rule.29history checks13.50s PASS, new settings live
save/reload acceptance pending final install. Existing full-suite29unrelated
baseline failures from personal-send task are not newly rerun or called GREEN.
