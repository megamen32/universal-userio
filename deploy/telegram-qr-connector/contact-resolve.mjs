// Resolve a Telegram username or phone number into a peer entity; never sends.
export async function resolveTelegramContact(client, identifier, Api) {
  const value = String(identifier || "").trim().replace(/^@/, "");
  if (!value || !client) return null;
  const phone = phoneNumber(value);
  if (phone) return await resolvePhone(client, phone, Api);
  try {
    return await client.getEntity(value);
  } catch {
    return null;
  }
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
