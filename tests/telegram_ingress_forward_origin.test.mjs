import assert from "node:assert/strict";
import test from "node:test";

import { bodyWithOrigin, forwardLine, forwardOrigin } from "../deploy/telegram-qr-connector/forward-origin.mjs";

test("a forwarded message keeps the place it came from", () => {
  const origin = forwardOrigin({
    fwdFrom: { fromName: "Канал Анонсов", fromId: { channelId: 123 } },
  });
  assert.deepEqual(origin, { name: "Канал Анонсов", peerId: "123", saved: false });
  assert.equal(forwardLine(origin), "Переслано от: Канал Анонсов");
});

test("a forward without a name still names its source", () => {
  const origin = forwardOrigin({ fwdFrom: { fromId: { userId: 555 } } });
  assert.equal(forwardLine(origin), "Переслано от: источник 555");
});

test("a saved message says it was saved, not forwarded", () => {
  const origin = forwardOrigin({
    fwdFrom: { savedFromName: "Избранное", savedFromId: { channelId: 9 } },
  });
  assert.equal(origin.saved, true);
  assert.equal(forwardLine(origin), "Сохранено от: Избранное");
});

test("a message that was not forwarded carries no origin", () => {
  assert.equal(forwardOrigin({ message: "привет" }), null);
  assert.equal(forwardLine(null), "");
  assert.equal(bodyWithOrigin("привет", null), "привет");
});

test("the origin joins the body without losing the text", () => {
  const origin = forwardOrigin({ fwdFrom: { fromName: "Канал" } });
  const body = bodyWithOrigin("текст сообщения", origin);
  assert.equal(body, "Переслано от: Канал\n\nтекст сообщения");
  assert.ok(body.includes("текст сообщения"));
});

test("the origin survives the body limit", () => {
  const origin = forwardOrigin({ fwdFrom: { fromName: "Канал" } });
  const body = bodyWithOrigin("я".repeat(9000), origin, 8000);
  assert.ok(body.length <= 8000);
  assert.ok(body.startsWith("Переслано от: Канал"));
});
