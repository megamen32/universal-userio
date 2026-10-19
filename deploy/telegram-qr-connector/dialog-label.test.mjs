import assert from "node:assert/strict";
import test from "node:test";
import { entityLabel, resolveDialogLabel } from "./dialog-label.mjs";

test("fresh Telegram user and group names replace cached numeric or old labels", () => {
  assert.equal(resolveDialogLabel({ id: 77, firstName: "Nikita", lastName: "Rozanov" }, "77"), "Nikita Rozanov");
  assert.equal(resolveDialogLabel({ id: -10042, title: "New group title" }, "Old group title"), "New group title");
});

test("incomplete Telegram entities preserve the latest known name", () => {
  assert.equal(resolveDialogLabel({ id: 77, min: true }, "Known contact"), "Known contact");
});

test("active alternate usernames are used when Telegram omits first and last names", () => {
  assert.equal(entityLabel({ id: 77, usernames: [
    { username: "old_alias", active: false, editable: false },
    { username: "current_alias", active: true, editable: false },
  ] }), "current_alias");
});
