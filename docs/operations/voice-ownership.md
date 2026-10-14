# Владелец звонков

UserIO владеет вызовом, ожиданием ответа и подключения, аудиопотоком, записью
и завершением. Канонический голосовой движок: `src/universal_userio/voice/`;
Matrix/Telegram transport workers находятся в основном пакете UserIO.
Notice Place определяет, какой инцидент требует уведомления, применяет тихие
часы ко всем автоматическим каналам и передаёт инцидент в UserIO. Exmanager
владеет адаптером своего секретаря и его диалоговой логикой.

ServersAdministartion хранит топологию, инструкции SSH/SIP/Android и сведения
о размещении. Старый каталог ai-bridge сохраняется как исторический источник
для незавершённого чужого WIP/PR5; новый runtime больше не исполняет его исходники.
Новые продуктовые правки движка публикуются из UserIO. Принятый код перенесён
из опубликованных Admin commits74fb73dc,c1d246f66d,7e6d46ce12; сохранены все
записи, конфигурация, банковские/внешние действия и прежние результаты.

Совместимые пути `/usr/local/lib/agentcall/*.py` ведут на опубликованный runtime
UserIO. Адаптер `agentcall_secretary_voice.py` ведёт на опубликованный Exmanager.
PCM greeting/wait-prompts и приватные настройки остаются в существующих
операторских каталогах; `AGENTCALL_ASSET_ROOT` по умолчанию указывает туда же.
Никакой новой очереди, модели, токена или демона перенос не создаёт.

Автоматические звонки:00:00–12:00 Europe/Moscow; все не emergency отменяются
до резервирования отправки. Уже неизвестные provider outcomes не повторяются.
Прямой контролируемый пользовательский вызов имеет отдельное явное назначение.
Ожидание ответаTelegram120с и подключения60с не входит во время разговора.

Бюджет gateway сохраняется: RAM1.5/2GiB, CPU200%, swap512MiB,tasks128,IOWeight50,
безGPU; один voice transport одновременно. Исходные проверки768MiB hard,
CPU2,tasks128,swap256MiB,IO10,temp128MiB; expected40s/emergency400s.

По просьбе владельца15октября Telegram setup увеличен: ответ120с/соединение60с.
Внешние gateway/manager окна разговора+340с; NoticePlace535с, HTTP550с.
Это конечное время на подготовку/ожидание, без повышенияCPU/RAM/parallelism;
время разговора начинается после установления медиа. Отказ собеседника или
завершение вызова самимTelegram прекращает ожидание раньше.

Telegram handshake budget15Oct: native8.0.0 fixture on this66-interface host
produced53packets/207307bytes (largest10358bytes). Two signalling queues are
bounded256KiB and256items each; up to8 outgoing RPCs per active voice transport,
also capped8 in the parent. This replaces64 eagerly created callback tasks and
one serial parent RPC. Native peer packets wait until connect_p2p initialization.
Fresh admission measurement: hostavailable33GiB; core197MiB/6tasks under384/512MiB,
64tasks; gateway40MiB/2tasks under1.5/2GiB,128tasks. Existing cgroup limits stay
unchanged; queue allowance512KiB aggregate, no extra provider account or worker.
Real recipient canary must confirm this budget after installation.
