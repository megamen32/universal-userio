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
