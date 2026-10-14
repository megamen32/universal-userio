# Передача голосового контекста секретарю

Workers Matrix/Telegram передают AgentCall `voice_transport` и фактический
counterparty из принятого звонка. Identity не берётся из произнесённого текста.
Private opt-in brain и подписанный native Secretary API принадлежат Exmanager.
Существующие RTC signaling, audio, provider sessions и записи сохраняются.

Внутренние call managers принимают agent_context от NoticePlace: incident_id,
title/body. Контекст хранится с durable call и проверяется при повторе запроса.
Уже принятые старые звонки не повторяются и не меняют контекст. Публичный MCP
не получает произвольного agent_context; обычный голосовой call остаётся
Secretary по умолчанию только для явно проверенных person mappings.

Зависимости review: Exmanager codex/secretary-fast-voice и ServersAdministartion
codex/secretary-voice-engine. Нужен actual recipient test в установленном SDK,
не только статический capability или ответ mock. Входящие Matrix/TG не добавлены.

Остановка API останавливает его Requires-dependent ingress службы. Перед
scoped install сохранить список ранее active RequiredBy/BoundBy/ConsistsOf;
после старта API восстановить только этот список, проверить каждый источник
и archive consumer. Старые неизвестные отправки не повторять. Повторный start
API сам по себе не восстанавливает Requires-dependent службу.

AgentCall event ingress после восстановления14Oct использовал около12MiB/1task;
при36GiB доступной RAM и24GiB резерва установлен конечный budget128MiB high/max,
swap16MiB/CPU50%/tasks16/IOWeight50. Это не speech worker: он читает bounded
JSONL и отправляет текстовые события, не хранит PCM и не использует GPU/temp.
