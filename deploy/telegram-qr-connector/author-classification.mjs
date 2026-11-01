// Internal provider metadata; caller must pass a verified Telegram User entity.
export function withTelegramAuthorClassification(routing, author) {
  if (!author || !/^[1-9][0-9]*$/.test(author.id) || typeof author.is_bot !== "boolean") {
    throw new Error("verified Telegram author required");
  }
  const metadata = routing ? JSON.parse(routing.provider_ref) : { author_id: author.id };
  if (metadata.author_id && metadata.author_id !== author.id) {
    throw new Error("Telegram author classification conflict");
  }
  metadata.author_id = author.id;
  metadata.author_is_bot = author.is_bot;
  return { ...(routing || { kind: "telegram_routing" }), provider_ref: JSON.stringify(metadata) };
}
