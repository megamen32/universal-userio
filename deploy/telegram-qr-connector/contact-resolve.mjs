// Resolve a Telegram username or phone number into a peer entity; never sends.
export async function resolveTelegramContact(client, identifier, Api) {
  const value = String(identifier || "").trim().replace(/^@/, "");
  if (!value || !client) return null;
  const phone = phoneNumber(value);
  if (phone) return await resolvePhone(client, phone, Api);
  try {
    // getEntity rewrites USERNAME_NOT_OCCUPIED into an untyped exception.
    // The same read RPC preserves the native reason before that conversion.
    const result = await client.invoke(new Api.contacts.ResolveUsername({username:value}));
    const peer = result && result.peer;
    const id = peer && (peer.userId ?? peer.channelId ?? peer.chatId);
    const entities = peer && peer.userId != null ? result.users : result.chats;
    const entity = (entities || []).find(item => String(item.id) === String(id));
    if (!entity) throw Object.assign(Error('invalid identity response'),{errorMessage:'INVALID_RESPONSE'});
    return entity;
  } catch (error) {
    throw identityReadError(error);
  }
}

function identityReadError(error) {
  const native = String(error && (error.errorMessage || error.code) || '');
  const name = error && error.constructor && error.constructor.name;
  const flood = /^FLOOD_(?:PREMIUM_)?WAIT(?:_\d+)?$/.test(native)
    || name === 'FloodWaitError' || name === 'FloodPremiumWaitError';
  const allowed = new Set(['USERNAME_NOT_OCCUPIED','USERNAME_INVALID','TIMEOUT',
    'ETIMEDOUT','ECONNRESET','ECONNREFUSED','AUTH_KEY_UNREGISTERED','SESSION_REVOKED',
    'USER_DEACTIVATED','INVALID_RESPONSE']);
  const code = flood ? (native.startsWith('FLOOD_PREMIUM') || name === 'FloodPremiumWaitError'
    ? 'FLOOD_PREMIUM_WAIT' : 'FLOOD_WAIT')
    : allowed.has(native) ? native : 'UNKNOWN_NATIVE_ERROR';
  const seconds = Number(error && error.seconds || native.match(/_(\d+)$/)?.[1]);
  // Preserve only typed, non-private fields through the existing HTTP error string.
  return Error(JSON.stringify({schema:'userio.identity-read-error.v1', code,
    retry_after_seconds:flood && Number.isSafeInteger(seconds) && seconds>0 ? seconds : null,
    observed_at:Date.now()/1000}));
}

// A phone number has to be known to the account before Telegram can resolve it.
async function resolvePhone(client, phone, Api) {
  const contacts = Api && Api.contacts;
  if (!contacts || !contacts.ImportContacts || !contacts.ResolvePhone) return null;
  try {
    await client.invoke(new contacts.ImportContacts({
      contacts: [{ phone, firstName: "UserIO" }],
    }));
  } catch {
    // The number may already be known, or the account may not allow the import.
  }
  try {
    const resolved = await client.invoke(new contacts.ResolvePhone({ phone }));
    const found = resolved && resolved.contacts && resolved.contacts.found;
    const user = found && found[0] && found[0].users && found[0].users[0];
    return user || null;
  } catch {
    return null;
  }
}

function phoneNumber(value) {
  const raw = value.replace(/[\s()\-.]/g, "");
  if (!/^\+?\d{7,15}$/.test(raw)) return "";
  const digits = raw.replace(/\D/g, "");
  return digits.length >= 7 && digits.length <= 15 ? `+${digits}` : "";
}

// The peer label the approved send addresses by id.
export function peerProfile(entity) {
  if (!entity) return { peer_id: "", username: "", name: "", phone: "" };
  const username = String(entity.username || "");
  const name = [entity.firstName, entity.lastName].filter(Boolean).join(" ").trim();
  return {
    peer_id: "", username,
    name: name || username || "",
    phone: String(entity.phone || ""),
  };
}
