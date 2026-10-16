import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url), "utf8");
const handler = source.match(/document\.addEventListener\("submit", function\(e\)\{[\s\S]*?\n\}\);/)[0];
const readBody = source.slice(source.indexOf("function readBody(req)"), source.indexOf("function safeFilename"));

// Execute the actual browser submit handler and actual server body parser.
// No browser, login, provider request or real secret is involved.
for (const [field, value] of [["password", "dummy+&= пароль"], ["code", "12345"], ["phone", "+79990000000"]]) {
  test(`Telegram ${field} form reaches the server parser intact`, async () => {
    let submit, captured, prevented = false;
    const form = { dataset: { async: "1" }, getAttribute: () => `/${field}?slot=local-test` };
    const fields = new FormData(); fields.set(field, value);
    const context = {
      document: { addEventListener: (_name, fn) => { submit = fn; } },
      FormData: class { constructor() { return fields; } }, URLSearchParams,
      fetch: (url, options) => { captured = { url, ...options }; return Promise.resolve(); },
      poll: () => {},
    };
    vm.runInNewContext(handler, context);
    submit({ target: form, preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    const request = new Request(`http://127.0.0.1${captured.url}`, captured);
    const parser = vm.runInNewContext(`${readBody}\nreadBody`, { URLSearchParams });
    const stream = new EventEmitter();
    const parsed = parser(stream);
    stream.emit("data", await request.text()); stream.emit("end");
    assert.equal((await parsed).get(field), value);
  });
}
