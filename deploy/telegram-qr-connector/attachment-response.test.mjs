import assert from "node:assert/strict";
import { validateHeaderValue } from "node:http";
import test from "node:test";

import { attachmentResponseHeaders } from "./attachment-response.mjs";

test("Unicode Telegram filenames use safe ASCII headers and preserve the original name", () => {
  const filename = "Приложение № 3.xlsx";
  const headers = attachmentResponseHeaders(filename);

  assert.doesNotThrow(() => validateHeaderValue("Content-Disposition", headers["content-disposition"]));
  assert.doesNotThrow(() => validateHeaderValue("X-Filename", headers["x-filename"]));
  assert.match(headers["content-disposition"], /filename="[\x20-\x7e]*"/);
  const encodedName = headers["content-disposition"].match(/filename\*=UTF-8''([^;]+)/)?.[1];
  assert.equal(decodeURIComponent(encodedName), filename);
  assert.equal(headers["x-filename"], "__________ _ 3.xlsx");
});

test("Telegram filename headers cannot inject response headers", () => {
  const headers = attachmentResponseHeaders('x.xlsx\r\nX-Injected: yes');
  assert.doesNotThrow(() => validateHeaderValue("Content-Disposition", headers["content-disposition"]));
  assert.doesNotThrow(() => validateHeaderValue("X-Filename", headers["x-filename"]));
  assert.ok(!headers["content-disposition"].includes("\r"));
  assert.ok(!headers["content-disposition"].includes("\n"));
});
