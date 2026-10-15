# Приёмка UserIO: три контейнера

Дата15.10.2026. Владелец: сессия01a13f72-1358-7332-9d9f-e107f5302085.
Private evidence: `.tmp/two-container-20261015/`; секреты и приватные JSON
не публикуются. Проверки не являются приёмкой соседних milestone.

## Установка и сохранность
- Ровно userio-frontend/userio-backend/userio-voice healthy;11backend и6voice
  управляемых процессов. Старые16локальных units отключены,44RTC отключён.
- App image b81ef6c: sha256:d7edc1c23752ab3d6d70feb383c1753404d473d3808ad2f5264544ee6c17a664.
- Host helper03f8ba9, SHA2568da05f06650f8de85d042a1bf791475b8892a25fb6131a089088eb43c6dd802d.
- После исправления UI frontend79b3587 image
  sha256:94746be86ae39c2d66065847b49f55465134087b4bb0cd6ff830391db616a128.
  Опубликованная static наложена на прежний immutable frontend8fe8c074;
  nginx/config неизменны,сборочный контекст471040B без чужого WIP.
-17account identities/provider/credential refs/capabilities совпали с baseline;
 241accepted messenger_actions целиком неизменны; сохранён fingerprint ключей.
- Persistent DB/files/sessions/RTC7terminalrecords остались на прежних volumes.
- Backend/voice start times16:17:39Z/16:35:30Z не изменились при UI update.

## Реальный потребитель
- MCP Bearer и OAuth:17аккаунтов каждый,те же связи.
- MAX fresh provider history12,оба ранее принятых private attachments по1разу.
- Telegram native reconcile scan3 fetched3,без send/ACK. MCP refresh для
  Telegram unsupported — это не потеря авторизации,маршрут не подменялся.
- Native IMAP UIDsearch4/4; Яндекс свежий poll14.3s; remote archive4complete.
- Backend→voice существующее RTCaudio6067212B совпало;7terminalrecords.
- Silero WAV117644B/24kHz/2.45s без внешнего звонка.
- Реальный native S21 guard: собственная аренда→container check valid→system
  acquire refusal75→finally release/free. Не было управления телефоном/звонка.
- Голос перезапущен отдельно; API/MCP17аккаунтов оставались доступны.
- Headed managed browser44: сохранён собственный вход через перенос и restart;
  три прежних company PDF и private txt/png скачаны с точными исходными SHA256.
  Повторных отправок в компанию или Избранное не было.
- Финальный browser logout подтвердил /login и401; собственный браузер закрыт.

## Замечание о боковой панели
До исправления реальный viewport263px содержал340.67px контента (overflowtrue).
После79b3587: desktop1050x613 и mobile390x844,content263==viewport263,
overflowfalse,outside0,17accountbuttons. Desktop/mobile screenshot просмотрены.
При390px также нет горизонтального overflow документа. Новый bundle загружен
публичным HTTPS после reload,старые workers не перезапускались. Собственный
браузер работал в конечном native44unit1/2GiB,CPU2,tasks384,swap0,wall600s.

## Проверки и конечные ресурсы
- Fast unit:23container-runtime/Telemost/SIP cases PASS0.39s,expected8s/max80s,
 768MiB/CPU1/tasks128/swap0. Проверяют supervise/health/graceful stop/library.
- Focused integration:5platform stdlib cases PASS0.515s на44,expected2s/max20s,
 768MiB/CPU1/tasks128/swap0. Выявляют duplicate/stale lease и lock contention,
 сохраняют UNKNOWN transcription без replay. Первичная среда44не имела pytest;
 сами проверки переведены вstdlib,не заявляется GREEN того неудачного запуска.
- Focused integration: UI tsc -b + Vite PASS; Vite1.05s,общийwallне измерен,
 expected30s/max300s,768MiB/CPU2/tasks128/swap0,temp256MiB/artifact1GiB на44.
- UI image packaging0.765s,expected12s/max120s,256MiB/CPU1/tasks64/swap0;
 Docker step limits128MiB/CPU0.5,контекст471KiB. Только COPY/LABEL,нет RUN.
- Initial backend build215.81s/frontend12.83s; полный voice buildexit0,
 export182.4s; полныйwallне инструментирован. Builder1GiB/CPU2/tasks256,
 swap0,parallelism1/cache6GiB; источник b81переиспользован при defer75.
- Slow nightly не запускался. Timeout/неполные попытки не объявлялись GREEN.
 Непринятый blank screenshot после первого browser deadline сохранён отдельно;
 итоговые проверки выполнены заново в конечном unit с guard перед каждым CLI.

## Граница результата
WhatsApp имел0B creds.json и QR refs attempts ended ещё до переноса; работающего
входа не было,новую авторизацию не выдаём за сохранённую. Нужен штатный QR-вход
владельца. Новых реальных звонков проверка не включала. Остальные принятые
проверки и старые результаты сохранены. Чужие Telegram/messenger/Graphify/phase17
WIP не входили в сборку или commits; весь checkout не объявляется чистым.

## Дополнение: выложен настоящий новый сайт
Текущее поручение заменило in-chat prototype настоящим UI на публичном URL.
Frontend source c081aa9/image e059cf68;API/store fc72254/image22f77252
поверх прежнего b81app;voice image/start16:35:30Z неизменны. Все3healthy,
publicHTTPS200;после backend restart own cookie сохраняет вход. NativeMCP17
account identities точно совпали с исходными id/provider.

Fast unit6PASS0.283s(expected1/max10s),256MiB/CPU1/tasks32/swap0.
Focused integration4PASS4.87s(expected4/max40s),768MiB/CPU1/tasks128/swap0:
созданы103локальные fixture-чата,старые IDs отсутствуют в latest100,но
conversation_ids возвращает только выбранные,проверены другая учётная запись,
source intersection,auth и invalid/missing IDs. Первая попытка этого нового
теста не назначила fixture route другому пользователю;после корректного
fixture bind PASS. Производственный отказ не скрывался под этим падением.

UI tsc -b и Vite PASS после каждой значимой правки;финальный Vite1.10s,
полныйwallне измерен,max300s,44source768MiB/CPU2/tasks128/swap0. Backend и
frontend immutable delta packaging1.466s;последний frontend0.754s,256MiB/
CPU1/tasks64/max120s,без RUN,из опубликованных owned inputs. Nightly не запускался.

Headed managed44:2TGchoices,selectedaccount remembered,вторая учётка30rows
ровноиз её API scope. Стандартныеpersonal47rows,все соответствуют metadata;
unread rows имеют unreadbadge. Сохранена папка с MAXprivate+почтой,2строго
выбранные строки→reload→rename→browser restart→редактор2checked→delete.
После удаленияprivatechatGET200. Existing txt123B и PNG179B скачаны с прежними
SHA25633a4be10… иdf14b3e5…. Никаких новых sends/calls/provider writes.

Final desktop1280x900,mobile390x844/320x740. Picker insideviewport;редактор
100choices/2checked,footerbuttonsinside,no horizontal scroll. Обнаруженные
mobile defects исправлены: светлый/горизонтальный scrollbar и исчезавший
заголовок чата(titleWidth0→90px). Final screenshot просмотрен,header/document
overflowfalse,composerinput иfilepicker доступны; advancedsettings открываются.

Private artifacts new-inbox-*-final.png иsource/build/test logs в прежнем
.tmp root.11Chrome процессов имели cgroup конечного собственного browserunit
1/2GiB,CPU2,tasks384,swap0,max900s/storage128MiB. Собственный logout/login401
подтверждён,finish закрывает только свою сессию. Папки per-browser с явным
описанием в редакторе;до64папок×100IDs. Внешний WhatsApp QR остаётся прежней
границей подключения,миграция не создавала отсутствующую авторизацию.
