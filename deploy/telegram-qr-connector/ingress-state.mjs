export function publicIngressState(slots, syncs, liveSlots) {
  return [...slots.entries()].map(([id, item]) => {
    const sync = syncs.get(id) || {};
    const live = liveSlots.get(id) || {};
    return {
      id,
      accountId: live.accountId || item.accountId || "",
      status: item.status,
      name: item.name || "",
      qr: item.status === "waiting" && item.qr ? item.qr : "",
      phone: item.phone || "",
      mode: item.mode || "qr",
      passwordHint: item.status === "password-required" ? (item.passwordHint || "") : "",
      codeViaApp: item.status === "code-required" ? !!item.codeViaApp : false,
      promptSeq: item.promptSeq || 0,
      sync: sync.running ? (sync.status || "on") : "",
      syncChats: sync.chats || 0,
      syncError: sync.status === "retrying" ? (sync.lastError || "") : "",
      lastSyncAt: Number(sync.lastSyncAt || 0),
    };
  });
}
