// Live authorization classification for a saved telegram-qr session.
//
// GramJS `client.isUserAuthorized()` swallows the provider answer and maps
// every RPCError to false, so a revoked auth key and a slow network look
// identical. Official TL schema (core.telegram.org/schema/mtproto,
// auth.Authorization / updates.getState) plus Android/tweb behaviour: the
// auth state is carried by the RPC error, not by the boolean.

const REVOKED_KEY_CODES = new Set([
  "AUTH_KEY_UNREGISTERED",
  "AUTH_KEY_INVALID",
  "AUTH_KEY_DUPLICATED",
  "SESSION_REVOKED",
  "SESSION_EXPIRED",
]);

// Server-reported availability problems: the key may still be valid, so the
// slot must retry instead of demanding a fresh login.
const TRANSIENT_CODES = new Set([
  "TIMEOUT",
  "FLOOD_WAIT",
  "SLOWMODE_WAIT",
  "INTERNAL_SERVER_ERROR",
  "SERVER_UNAVAILABLE",
  "NETWORK_ERROR",
  "CONNECTION_ERROR",
]);

export function classifyAuthError(error) {
  const raw = String((error && (error.errorMessage || error.message)) || "").trim();
  const code = raw.replace(/[^A-Z_]/gi, "_").toUpperCase();
  if (REVOKED_KEY_CODES.has(code)) return { authorized: false, terminal: true, code: "revoked_auth_key", provider_code: code };
  if (code === "SESSION_PASSWORD_NEEDED") return { authorized: false, terminal: true, code: "session_password_needed", provider_code: code };
  if (TRANSIENT_CODES.has(code) || code === "") return { authorized: false, terminal: false, code: "temporarily_unavailable", provider_code: code || "unknown" };
  return { authorized: false, terminal: false, code: "authorization_unavailable", provider_code: code };
}

export async function probeAuthorization(client, Api) {
  // Same request GramJS uses internally, but the real error is kept.
  try {
    await client.invoke(new Api.updates.GetState());
    return { authorized: true, terminal: false, code: "authorized", provider_code: "" };
  } catch (error) {
    return classifyAuthError(error);
  }
}

// A slot whose auth key was revoked can never authorize again with the saved
// session; only a fresh login can. Reconnecting every syncRetrySeconds only
// produces identical log lines, so back off far instead of hammering the DC.
export function authRetryDelaySeconds(base, verdict) {
  const seconds = Number(base) > 0 ? Number(base) : 30;
  return verdict && verdict.terminal ? Math.max(seconds, 900) : seconds;
}