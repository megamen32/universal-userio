import {textHash} from './dot-relay.mjs';
// Read-only proof selection; callers fence writers and durably back up state.
export function verifiedIncomingEcho(record,snapshot,room,expectedHash){
 if(!record||!['send_uncertain','submitted_unconfirmed','echo_unknown','echo_timeout_requires_review','echo_requires_review'].includes(record.status)||record.text_hash!==expectedHash||!record.cursor||!snapshot?.ok||snapshot.room!==room||!Array.isArray(snapshot.messages))throw Error('incoming_reconcile_invalid');
 const matches=snapshot.messages.filter(m=>m.role==='user'&&typeof m.text==='string'&&textHash(m.text.trim())===expectedHash&&typeof m.id==='string'&&m.id.startsWith(room+'~'+room+'~CalpicoMessage~'));
 if(matches.length!==1)throw Error('incoming_reconcile_not_unique');
 return matches[0].id;
}
