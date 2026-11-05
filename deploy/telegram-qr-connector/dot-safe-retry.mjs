// Only pre-submit failures are retryable. The caller must hold the relay fence,
// verify provider source identity, and durably back up state before invoking.
export function claimNotAccepted(record,expectedHash,approvalId,backup){
 if(!record||record.status!=='not_accepted'||!['snapshot_unavailable','wrong_or_unloaded_room'].includes(record.reason)||record.cursor||record.echo||record.text_hash!==expectedHash||!/^[a-f0-9]{32}$/.test(approvalId)||!backup)throw Error('retry_not_proven_safe');
 const prior={status:record.status,reason:record.reason,at:Date.now(),approval_id:approvalId,backup};
 record.status='queued';delete record.reason;(record.safe_retries||=[]).push(prior);return prior;
}
export function validateReplayReply(record,replyTo,resolve){
 const source=replyTo?String(replyTo):null;
 if((record.provider_reply_to||null)!==source)throw Error('retry_reply_changed');
 if(source&&(!record.reply_to_main||resolve(source)!==record.reply_to_main))throw Error('retry_reply_unverified');
 return true;
}
