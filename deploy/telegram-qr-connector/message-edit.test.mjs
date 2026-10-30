import assert from "node:assert/strict";
import test from "node:test";
import { editOwnedTelegramText, TelegramEditRefused } from "./message-edit.mjs";

class EditMessage {
  constructor(value) { Object.assign(this, value); }
}

function fixture(message) {
  const calls = [];
  const current = { id: 77, message: "before", out: true, editDate: 0, ...message };
  const client = {
    async getMessages(_peer, { ids }) {
      assert.deepEqual(ids, [77]);
      return [{ ...current }];
    },
    async invoke(request) {
      calls.push(request);
      current.message = request.message;
      current.entities = request.entities || [];
      current.editDate = 123;
    },
  };
  return { client, calls, api: { messages: { EditMessage } }, current };
}

test("edits one own text message only after a fresh exact-text read and verifies it", async () => {
  const f = fixture({});
  const result = await editOwnedTelegramText({
    ...f, peer: "peer", messageId: 77, expectedText: "before", text: "after",
  });
  assert.equal(f.calls.length, 1);
  assert.ok(f.calls[0] instanceof EditMessage);
  assert.deepEqual({ ...f.calls[0] }, { peer: "peer", id: 77, message: "after", noWebpage: false });
  assert.deepEqual(result, { messageId: "77", text: "after", editDate: 123, out: true });
});

test('edits a markdown response with provider-verified bold formatting', async () => {
  const f = fixture({});
  const result = await editOwnedTelegramText({
    ...f, peer: 'peer', messageId: 77, expectedText: 'before',
    text: '**Готово**', format: 'telegram_markdown',
  });
  assert.equal(f.calls[0].message, 'Готово');
  assert.equal(f.calls[0].entities[0].className, 'MessageEntityBold');
  assert.equal(result.text, 'Готово');
});

test('does not claim a formatted edit when Telegram readback omits its entities', async () => {
  const f = fixture({});
  const read = f.client.getMessages;
  f.client.getMessages = async (...args) => {
    const result = await read(...args);
    if (f.calls.length) result[0].entities = [];
    return result;
  };
  await assert.rejects(editOwnedTelegramText({
    ...f, peer: 'peer', messageId: 77, expectedText: 'before',
    text: '**Готово**', format: 'telegram_markdown',
  }), /outcome is unknown/);
  assert.equal(f.calls.length, 1);
});

test("refuses an incoming or stale message before invoking Telegram", async (t) => {
  for (const [name, message, expectedCode] of [
    ["incoming", { out: false }, "not_own_message"],
    ["changed", { message: "changed elsewhere" }, "source_changed"],
  ]) {
    await t.test(name, async () => {
      const f = fixture(message);
      await assert.rejects(
        editOwnedTelegramText({ ...f, peer: "peer", messageId: 77, expectedText: "before", text: "after" }),
        error => error instanceof TelegramEditRefused && error.code === expectedCode && error.submitted === false,
      );
      assert.equal(f.calls.length, 0);
    });
  }
});

test("refuses captions and media edits before invoking Telegram", async () => {
  const f = fixture({ media: { className: "MessageMediaPhoto" } });
  await assert.rejects(
    editOwnedTelegramText({ ...f, peer: "peer", messageId: 77, expectedText: "before", text: "after" }),
    error => error instanceof TelegramEditRefused && error.code === "media_edit_not_supported",
  );
  assert.equal(f.calls.length, 0);
});
