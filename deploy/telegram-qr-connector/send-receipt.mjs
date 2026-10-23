// The receipt of an approved send.
//
// The connector journals every approved attempt, so a repeated approval
// answers from that journal instead of sending the message a second time.
// A reply also names the source it answers and the topic it lives in: a
// repeated reply must carry the same binding, or the caller cannot tell
// which message the receipt belongs to.

export function duplicateReceipt(recorded) {
  const receipt = {
    ok: true, duplicate: true, slot: recorded.slot,
    account_id: recorded.account_id, peer_id: recorded.peer_id,
    message_id: String(recorded.message_id), date: recorded.date,
  };
  if (recorded.kind === "reply") {
    receipt.reply_to_id = recorded.reply_to_id;
    receipt.thread_id = recorded.thread_id || "";
  }
  return receipt;
}

// The durable fields of a source-bound reply. The journal keeps them, so the
// repeated approval of the same draft answers with the same source.
export function replyAttemptFields(sent) {
  return { reply_to_id: sent.replyToId, thread_id: sent.threadId || "" };
}
