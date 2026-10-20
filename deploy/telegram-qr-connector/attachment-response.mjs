function rfc5987(value) {
  return encodeURIComponent(value).replace(/[!'()*]/g, (character) =>
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}

export function attachmentResponseHeaders(filename) {
  const safeName = String(filename || "telegram-media").trim()
    .replace(/[\\/\x00-\x1f\x7f"]/g, "_") || "telegram-media";
  const fallback = safeName.replace(/[^\x20-\x7e]/g, "_");
  return {
    "content-disposition": `attachment; filename="${fallback}"; filename*=UTF-8''${rfc5987(safeName)}`,
    "x-filename": fallback,
  };
}
