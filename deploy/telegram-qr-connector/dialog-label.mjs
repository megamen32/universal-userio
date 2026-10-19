function alternateUsername(entity) {
  const usernames = Array.isArray(entity && entity.usernames) ? entity.usernames : [];
  const current = usernames.find((item) => item && item.active && item.username);
  return current ? String(current.username) : "";
}

export function entityName(entity) {
  if (!entity) return "";
  return String(
    entity.title
    || [entity.firstName, entity.lastName].filter(Boolean).join(" ")
    || entity.username
    || alternateUsername(entity),
  ).trim();
}

export function entityLabel(entity) {
  if (!entity) return "telegram";
  return entityName(entity) || String(entity.id || "telegram");
}

export function resolveDialogLabel(entity, cachedLabel) {
  const fresh = entityName(entity);
  if (fresh) return fresh;
  const cached = String(cachedLabel || "").trim();
  if (cached && cached !== "telegram" && !/^[-+]?\d+$/.test(cached)) return cached;
  return entityLabel(entity);
}
