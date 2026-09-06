import { spawnSync } from "node:child_process";
import http from "node:http";
import https from "node:https";

const DEFAULT_ENDPOINT = "http://192.168.2.75:7653/v1/audio/transcriptions";
const DEFAULT_SECRET_GETTER = "/home/roomhacker/agents-projects/simple-secret-storage/bin/sss-get.mjs";
const DEFAULT_SECRET_NODE = "/usr/local/bin/node";

function value(obj, key, fallback) {
  return obj && obj[key] != null ? obj[key] : fallback;
}

export function telegramAudioDescriptor(message) {
  const media = value(message, "media", null);
  const document = value(media, "document", null);
  if (!document) return null;
  const mimeType = String(value(document, "mimeType", value(document, "mime_type", ""))).toLowerCase();
  const attributes = Array.isArray(document.attributes) ? document.attributes : [];
  const audioAttribute = attributes.find(function (attr) {
    const ctor = attr && attr.constructor ? attr.constructor.name : "";
    const name = String(value(attr, "className", ctor));
    return name.indexOf("DocumentAttributeAudio") >= 0 || value(attr, "voice", false) === true;
  });
  if (mimeType.indexOf("audio/") !== 0 && !audioAttribute) return null;
  const voice = Boolean(audioAttribute && audioAttribute.voice);
  let extension = "audio";
  if (mimeType.indexOf("ogg") >= 0) extension = "ogg";
  else if (mimeType.indexOf("mpeg") >= 0 || mimeType.indexOf("mp3") >= 0) extension = "mp3";
  else if (mimeType.indexOf("mp4") >= 0 || mimeType.indexOf("m4a") >= 0) extension = "m4a";
  else if (mimeType.indexOf("wav") >= 0) extension = "wav";
  else if (voice) extension = "ogg";
  return {
    kind: voice ? "voice" : "audio",
    contentType: mimeType || (voice ? "audio/ogg" : "application/octet-stream"),
    filename: "telegram-" + String(value(message, "id", "audio")) + "." + extension,
  };
}

export function loadWhisperApiKey(options) {
  options = options || {};
  const env = options.env || process.env;
  const spawn = options.spawn || spawnSync;
  const explicit = String(env.USERIO_STT_API_KEY || env.USERIO_WHISPER_API_KEY || env.GREPMESH_STT_API_KEY || "").trim();
  if (explicit) return explicit;
  const result = spawn(options.secretNode || DEFAULT_SECRET_NODE, [options.getter || DEFAULT_SECRET_GETTER, "KANBAN_WHISPER_API_KEY"], {
    encoding: "utf8",
    timeout: 20000,
    maxBuffer: 64 * 1024,
    env: env,
  });
  if (!result || result.status !== 0) return "";
  return String(result.stdout || "").trim();
}

function multipartBody(bytes, descriptor, boundary, model) {
  const prefix = Buffer.from(
    "--" + boundary + "\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n" + model + "\r\n" +
    "--" + boundary + "\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n" +
    "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + descriptor.filename.replace(/[\"\r\n]/g, "") + "\"\r\n" +
    "Content-Type: " + descriptor.contentType + "\r\n\r\n",
  );
  const suffix = Buffer.from("\r\n--" + boundary + "--\r\n");
  return Buffer.concat([prefix, Buffer.from(bytes), suffix]);
}

function parseTranscript(contentType, body) {
  const text = body.toString("utf8");
  if (String(contentType || "").indexOf("application/json") >= 0) {
    const parsed = JSON.parse(text || "{}");
    return String(parsed.text || parsed.transcript || "").trim();
  }
  return text.trim();
}

export function requestWhisper(bytes, descriptor, options) {
  options = options || {};
  const endpoint = new URL(options.endpoint || process.env.USERIO_STT_URL || process.env.USERIO_WHISPER_URL || DEFAULT_ENDPOINT);
  const apiKey = String(options.apiKey || "");
  const model = String(options.model || process.env.USERIO_STT_MODEL || process.env.USERIO_WHISPER_MODEL || "whisper-1");
  const timeoutMs = Number(options.timeoutMs || 600000);
  const boundary = "----userio-" + Date.now().toString(16) + Math.random().toString(16).slice(2);
  const body = multipartBody(bytes, descriptor, boundary, model);
  const transport = endpoint.protocol === "http:" ? http : https;
  return new Promise(function (resolve, reject) {
    const request = transport.request(endpoint, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + apiKey,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Content-Length": String(body.length),
      },
    }, function (response) {
      const chunks = [];
      response.on("data", function (chunk) { chunks.push(chunk); });
      response.on("end", function () {
        const responseBody = Buffer.concat(chunks);
        if (response.statusCode < 200 || response.statusCode >= 300) {
          resolve({ transcript: "", error: "whisper_http_" + response.statusCode });
          return;
        }
        try {
          const transcript = parseTranscript(response.headers["content-type"], responseBody);
          resolve({ transcript: transcript, error: transcript ? "" : "whisper_empty_transcript" });
        } catch (error) {
          resolve({ transcript: "", error: "whisper_invalid_response" });
        }
      });
    });
    request.setTimeout(timeoutMs, function () { request.destroy(new Error("timeout")); });
    request.on("error", reject);
    request.end(body);
  });
}

export async function transcribeTelegramAudio(client, message, options) {
  options = options || {};
  const descriptor = telegramAudioDescriptor(message);
  if (!descriptor) return null;
  const model = String(options.model || process.env.USERIO_STT_MODEL || process.env.USERIO_WHISPER_MODEL || "whisper-1");
  if (!options.apiKey) return Object.assign({}, descriptor, { transcript: "", status: "unavailable", model: model, error: "whisper_api_key_unavailable" });
  let bytes;
  try {
    bytes = await client.downloadMedia(message, {});
  } catch (error) {
    return Object.assign({}, descriptor, { transcript: "", status: "failed", model: model, error: "telegram_download_failed" });
  }
  if (!bytes || !bytes.length) return Object.assign({}, descriptor, { transcript: "", status: "failed", model: model, error: "telegram_download_empty" });
  try {
    const result = options.requestImpl
      ? await options.requestImpl(bytes, descriptor, options)
      : await requestWhisper(bytes, descriptor, options);
    return Object.assign({}, descriptor, result, {
      status: result.transcript ? "completed" : (result.error === "whisper_empty_transcript" ? "empty" : "failed"),
      model: model,
    });
  } catch (error) {
    return Object.assign({}, descriptor, { transcript: "", status: "failed", model: model, error: "whisper_failed" });
  }
}

export function bodyAndAttachments(message, audioResult) {
  const visible = String(value(message, "message", "")).trim();
  if (!audioResult) return { body: visible, attachments: [] };
  const attachment = {
    kind: audioResult.kind,
    content_type: audioResult.contentType,
    filename: audioResult.filename,
    provider_ref: String(value(message, "id", "")),
    transcript: String(audioResult.transcript || ""),
    transcription_status: String(audioResult.status || (audioResult.transcript ? "completed" : "failed")),
    transcription_model: String(audioResult.model || ""),
  };
  if (audioResult.transcript) {
    return {
      body: visible ? visible + "\n\n" + audioResult.transcript : audioResult.transcript,
      attachments: [attachment],
    };
  }
  return {
    body: visible || "[Telegram " + audioResult.kind + ": transcription unavailable]",
    attachments: [attachment],
  };
}
