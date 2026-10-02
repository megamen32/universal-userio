import assert from "node:assert";
import { bodyAndAttachments, loadWhisperApiKey, telegramAudioDescriptor, transcribeTelegramAudio } from "./transcription.mjs";

async function main() {

  function voice(id) {
    return { id: id || 7, message: "", media: { document: { mimeType: "audio/ogg", attributes: [{ className: "DocumentAttributeAudio", voice: true }] } } };
  }

  assert.strictEqual(telegramAudioDescriptor(voice()).kind, "voice");
  assert.strictEqual(telegramAudioDescriptor({ media: { document: { mimeType: "application/pdf", attributes: [] } } }), null);
  let called = false;
  assert.strictEqual(loadWhisperApiKey({ env: { USERIO_WHISPER_API_KEY: "abc" }, spawn: function () { called = true; } }), "abc");
  assert.strictEqual(called, false);
  const success = await transcribeTelegramAudio({ downloadMedia: async function () { return Buffer.from("ogg"); } }, voice(), {
    apiKey: "secret",
    requestImpl: async function () { return { transcript: "Привет из голоса", error: "" }; },
  });
  assert.strictEqual(success.transcript, "Привет из голоса");
  assert.deepStrictEqual(bodyAndAttachments(voice(), success), {
    body: "Привет из голоса",
    attachments: [{ kind: "voice", content_type: "audio/ogg", filename: "telegram-7.ogg", provider_ref: "7", transcript: "Привет из голоса", transcription_status: "completed", transcription_model: "whisper-1" }],
  });
  const failure = await transcribeTelegramAudio({ downloadMedia: async function () { return Buffer.from("ogg"); } }, voice(8), {
    apiKey: "secret",
    requestImpl: async function () { return { transcript: "", error: "whisper_http_503" }; },
  });
  const failedBody = bodyAndAttachments(voice(8), failure);
  assert.ok(failedBody.body.indexOf("transcription unavailable") >= 0);
  assert.strictEqual(failedBody.attachments[0].transcription_status, "failed");
  console.log("telegram transcription tests: ok");
}

main().catch(function (error) { console.error(error); process.exit(1); });
