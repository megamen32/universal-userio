export function loginAuthorized(headers, token) {
  const expected = String(token || "").trim();
  if (!expected) return false;
  const authorization = String((headers && headers.authorization) || "");
  return authorization === `Bearer ${expected}`;
}

export function normalizeLoginPhone(value) {
  const phone = String(value || "").replace(/[\s()-]/g, "");
  if (!/^\+[1-9][0-9]{7,14}$/.test(phone)) {
    throw new Error("phone must be an international number such as +79990001111");
  }
  return phone;
}

export function normalizeLoginCode(value) {
  const code = String(value || "").replace(/\s/g, "");
  if (!/^[0-9]{3,8}$/.test(code)) throw new Error("Telegram login code is invalid");
  return code;
}
