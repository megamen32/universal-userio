// Read-only dialog metadata; bounded pages, same connected client and account.
export function peerProfile(entity) {
  if (!entity || entity.min) return null;
  return { username: String(entity.username || ''), first_name: String(entity.firstName || ''),
    last_name: String(entity.lastName || ''), phone: String(entity.phone || '') };
}
export async function syncContactProfiles(client, accountId, post) {
  let batch = [], count = 0;
  const flush = async () => {
    if (!batch.length) return;
    await post('/v1/contact-profiles', { account_id: accountId, profiles: batch });
    count += batch.length; batch = [];
  };
  for await (const dialog of client.iterDialogs({ limit: 10000 })) {
    const profile = peerProfile(dialog.entity);
    if (!profile) continue;
    batch.push({ peer_id: String(dialog.id), ...profile });
    if (batch.length >= 100) await flush();
  }
  await flush(); return count;
}
