function routingMetadata(envelope) {
  for (const attachment of (envelope && envelope.attachments) || []) {
    if (!attachment || attachment.kind !== "telegram_routing") continue;
    try {
      const value = JSON.parse(String(attachment.provider_ref || ""));
      if (value && typeof value === "object") return value;
    } catch (_error) {}
  }
  return {};
}

export function buildAgentDeliverEvent(options) {
  const envelope = options.envelope || {};
  const parts = String(envelope.message_id || "").split(":");
  const messageId = parts.length ? parts[parts.length - 1] : "";
  const normalizedChatId = String(options.normalizedChatId || "");
  const chatLabel = String(options.label || normalizedChatId || "Telegram");
  const routing = routingMetadata(envelope);
  const authorName = String(routing.author_name || "").trim();
  const authorId = String(routing.author_id || "").trim();
  const ignoredChats = Array.from(new Set(
    (options.ignoredChats || []).map((value) => String(value).trim()).filter(Boolean),
  ));
  const hermesSessionId = String(options.hermesSessionId || "").trim();
  const eventId = `telegram-quiet:${normalizedChatId}:${messageId}`;

  return {
    schema: "gptadmin.agent-deliver.v1",
    event_id: eventId,
    source: {
      system: "telegram",
      type: "messages.quiet",
      ref: `telegram:${normalizedChatId}:${messageId}`,
    },
    target: {
      harness: "opencode",
      name: options.agentName,
      cwd: options.agentCwd,
    },
    subject: `Telegram · ${chatLabel}${authorName ? ` · последнее от ${authorName}` : ""}`,
    payload: {
      chat_id: normalizedChatId,
      chat_label: chatLabel,
      last_message_id: envelope.message_id,
      last_author: { id: authorId, name: authorName },
      message_count: options.messageCount,
      quiet_seconds: options.quietSeconds,
      task: {
        kind: "triage_and_reply_suggestions",
        read_with: {
          tool: "userio.channels.read",
          arguments: {
            channel: "telegram",
            message_id: envelope.message_id,
            ignored_chats: ignoredChats,
          },
        },
        instructions: [
          "Прочитай сообщение и при необходимости контекст чата через UserIO MCP; во всех read/list вызовах передавай ignored_chats.",
          "Начни с источника, названия чата и имени реального автора. Для группы различай авторов отдельных сообщений.",
          "Кратко объясни, чего хочет собеседник, что важно и требуется ли действие.",
          "Предложи три готовых варианта ответа: короткий, нейтральный и теплый.",
          "Не отправляй ответ самостоятельно; дождись выбора пользователя.",
          hermesSessionId
            ? `После подготовки запиши разбор во временный файл и выполни \`/opt/userio-telegram-qr/deliver-hermes-context.py --session-id ${hermesSessionId} --event-id ${eventId} --file <путь> --to telegram\`. Помощник сначала сохраняет разбор в контексте Hermes через append_delegation_delivery, затем вызывает hermes send --to telegram. Удали временный файл. Это уведомление владельцу: не отправляй в исходный чат и не выбирай вариант за пользователя.`
            : "После подготовки доставь весь разбор владельцу: запиши его во временный файл, вызови `/home/roomhacker/.hermes/hermes-agent/venv/bin/hermes send --to telegram --file <путь>`, затем удали файл. Это уведомление владельцу: не отправляй в исходный чат и не выбирай вариант за пользователя.",
        ],
      },
    },
    delivery: { create: "if_missing", activation: "always", mode: "sync" },
  };
}
