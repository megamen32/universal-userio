import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Provider-side message edits (Telegram bot status texts, "Начинаю проверку..."
// → "Найдено в топе: N") must reach the UserIO mirror immediately instead of
// waiting for the 5-minute reconciliation backfill.
const source = readFileSync(
  new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url),
  "utf8",
);

test("EditedMessage event builder is imported from its own module", () => {
  // events/index.js in GramJS 2.26 exports only Raw/NewMessage; importing
  // EditedMessage from there crashes the connector with a SyntaxError.
  assert.match(
    source,
    /import \{(?=[^}]*\bNewMessage\b)(?![^}]*\bEditedMessage\b)[^}]*\} from "telegram\/events\/index\.js";/,
  );
  assert.match(
    source,
    /import \{ EditedMessage \} from "telegram\/events\/EditedMessage\.js";/,
  );
});

test("edit handler is attached beside the new-message handler before backfill", () => {
  const syncStart = source.indexOf("async function syncAccount(slot)");
  const newHandler = source.indexOf("new NewMessage({})", syncStart);
  const editHandler = source.indexOf("new EditedMessage({})", syncStart);
  const backfill = source.indexOf("const chats = await backfillDialogs", syncStart);
  assert.ok(newHandler > syncStart, "NewMessage handler lives inside syncAccount");
  assert.ok(editHandler > newHandler, "EditedMessage handler is registered after NewMessage");
  assert.ok(backfill > editHandler, "both handlers attach before historical reconciliation");
});

test("edit ingestion never reschedules agent delivery", () => {
  const ingest = source.indexOf("async function ingestLive(");
  const guard = source.indexOf("if (!isEdit && !message.out)", ingest);
  const debounce = source.indexOf("debounceAgentDeliver(", ingest);
  assert.ok(ingest >= 0, "ingestLive exists");
  assert.ok(guard > ingest && debounce > guard, "debounceAgentDeliver stays behind the edit guard");
});

test("envelopes carry the provider edit timestamp when present", () => {
  assert.match(source, /message\.editDate \? \{ edited_at: Number\(message\.editDate\) \} : \{\}/);
});
