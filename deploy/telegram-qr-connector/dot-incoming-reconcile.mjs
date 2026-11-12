import {textHash,echoText} from './dot-relay.mjs';
// Read-only proof selection; callers fence writers and durably back up state.
export function verifiedIncomingEcho(record,snapshot,room,expectedHash,{captionRecovery=false}={}){
 const recoverable=captionRecovery&&record?.status==='requires_review'&&record?.reason==='caption_unverified'&&Array.isArray(record?.media)&&record.media.length>0;
 if(!record||(!recoverable&&!['send_uncertain','submitted_unconfirmed','echo_unknown','echo_timeout_requires_review','echo_requires_review'].includes(record.status))||record.text_hash!==expectedHash||!record.cursor||!snapshot?.ok||snapshot.room!==room||!Array.isArray(snapshot.messages))throw Error('incoming_reconcile_invalid');
 const expectedNames=(record.media||[]).map(item=>item.filename).sort();
 const matches=snapshot.messages.filter(m=>m.role==='user'&&typeof m.text==='string'&&(textHash(m.text.trim())===expectedHash||(record.echo_normalized_hash&&textHash(echoText(m.text))===record.echo_normalized_hash))&&typeof m.id==='string'&&m.id.startsWith(room+'~'+room+'~CalpicoMessage~')&&
  (!expectedNames.length||(m.attachment_count===expectedNames.length&&Array.isArray(m.attachment_names)&&JSON.stringify([...m.attachment_names].sort())===JSON.stringify(expectedNames))));
 if(matches.length!==1)throw Error('incoming_reconcile_not_unique');
 return matches[0].id;
}

// A prior caption failure stopped before send. A recovery of the prepared
// draft may be acknowledged only when the preflight, one-time intent and
// independently observed echo all refer to this exact source and staged file.
export function verifiedCaptionRecoveryAudit({proof,intent,result,record,id,requestId,echo,hash,messageId}){
 const original=proof?.before_relay?.incoming?.[id],group=proof?.before_media?.groups?.['message:'+messageId];
 const file=record?.media?.length===1?record.media[0]:null;
 if(!/^[a-f0-9]{32}$/.test(requestId||'')||!file||record.status!=='requires_review'||record.reason!=='caption_unverified'||record.echo||
   proof?.request_id!==requestId||proof.status!=='preflight_complete'||proof.operation!=='submit_existing_verified_draft_once'||proof.source_key!==id||
   JSON.stringify(original)!==JSON.stringify(record)||group?.status!=='requires_review'||group.echo!==null||JSON.stringify(group.media)!==JSON.stringify(record.media)||
   proof.source_sha256!==file.sha256||proof.source_size!==file.size||intent?.request_id!==requestId||intent.status!=='submit_started'||
   result?.status!=='submitted_echo_found'||result.echo!==echo||result.echo_text_hash!==hash)throw Error('caption_recovery_audit_invalid');
 return true;
}
