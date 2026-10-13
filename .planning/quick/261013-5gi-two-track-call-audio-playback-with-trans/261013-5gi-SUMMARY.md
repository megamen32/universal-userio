---
status: complete
---
# Записи звонков — две дорожки

В завершённых phone-диалогах отображается запись с отдельными дорожками «Собеседник»/«Телефон», нативными audio controls, совместным стартом и скачиванием WAV. Источник — реальные received/sent gateway PCM на общей шкале; направление не угадывается из текста. Прежние lifecycle/расшифровки сохраняются. Старые звонки без файлов не получают выдуманное аудио; incomplete явно отмечается.

Owned source 787077b опубликован/установлен; runtime manifest verified. Voice bridge/recorder не менялись; новых звонков/моделей/отправок нет.

12 focused integration checks PASS1.23s, bounded helper0, same-generation cleanup; штатный frontend build PASS, peak391.6MiB/swapOOM0. Неполная запись, account/peer/user isolation и byte ranges проверены на настоящих WAV fixtures. Обычный pytest warning об unknown asyncio config сохранён.

Настоящая headed44 web-приёмка PASS: существующий записанный звонок28.269125s, обычный account→dialog→две дорожки→одновременный playback→reload→карточка сохранена. Оба плеера playing/currentTime0.403s/errornull; расхождение12µs. Два скачанных файла по452350B имеют различные SHA и в точности совпадают с оригинальным приватным архивом. Screenshot inspected. Первый автоматический synthetic click не дал playback: FAIL сохранён; исправленный consumer использовал настоящий Input mouse click, source не менялся.

Оба собственных Chrome/Xvfb/cgroup завершены; headerclear/configabsent/native0/swapOOM0. Чужие браузеры/телефон/voice runtime untouched. Credential/PNG copies удалены после проверки; только безопасные private receipts остаются.

Proof: `.tmp/call-audio-20261013/{verify,web-proof,archive-byte-match}.json`; managed44 native cleanup receipts в собственной artifact case. Ограничение: это received/sent gateway audio, не запись звука у удалённого динамика и не звонки вне шлюза.

Остатки прежнего v1.3 MAX/Secretary milestone остаются blocked/pending по прежним доказательствам; эта независимая задача их не закрывает.
