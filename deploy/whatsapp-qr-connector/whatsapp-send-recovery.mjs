// UserIO WhatsApp send journal: exact original attempt only, no provider resend.
import { createHash, randomUUID } from 'node:crypto';
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeSync } from 'node:fs';
import { dirname } from 'node:path';

const digest = text => createHash('sha256').update(text).digest('hex');
const valid = text => typeof text === 'string' && text.length > 0 && text.length < 512;
const states = new Set(['pending','unknown','sent','not_submitted']);
function copy(row) { return row && JSON.parse(JSON.stringify(row)); }

export class WhatsAppSendJournal {
  constructor(path) {
    this.path = path;
    this.rows = new Map();
    if (existsSync(path)) {
      let payload = JSON.parse(readFileSync(path,'utf8'));
      if (!payload || payload.schema !== 1 || !Array.isArray(payload.rows)) {
        throw new Error('WhatsApp send journal corrupt: refuse to lose idempotency');
      }
      for (const row of payload.rows) {
        if (!valid(row?.attempt_id) || !states.has(row.state)
            || !valid(row.message_id) || !valid(row.chat_id)
            || !valid(row.slot) || !valid(row.body_sha256)
            || this.rows.has(row.attempt_id)) {
          throw new Error('WhatsApp send journal contains invalid or duplicate attempts');
        }
        this.rows.set(row.attempt_id, row);
      }
    }
  }

  save() {
    mkdirSync(dirname(this.path),{recursive:true,mode:0o700});
    const temp=this.path+'.tmp-'+randomUUID();
    let fd=null, dir=null;
    try {
      fd=openSync(temp,'wx',0o600);
      writeSync(fd,JSON.stringify({schema:1,rows:[...this.rows.values()]}));
      fsyncSync(fd);
      closeSync(fd);fd=null;
      renameSync(temp,this.path);
      dir=openSync(dirname(this.path),'r');fsyncSync(dir);
    } finally {
      if (fd!==null) closeSync(fd);
      if (dir!==null) closeSync(dir);
      if (existsSync(temp)) unlinkSync(temp);
    }
  }

  get(id){return copy(this.rows.get(id));}

  prepare({attempt_id,user_id,account_ref,slot,chat_id,body,reply_id='',message_id}) {
    if (![attempt_id,user_id,account_ref,slot,chat_id,message_id].every(valid)
        || typeof body!=='string' || !body.trim() || !/^[A-Za-z0-9_-]{8,128}$/.test(message_id)) {
      throw new TypeError('exact WhatsApp attempt/account/peer and preallocated message ID required');
    }
    const identity={attempt_id,user_id,account_ref,slot,chat_id,message_id,
      body_sha256:digest(body),reply_id:String(reply_id||'')};
    const existing=this.rows.get(attempt_id);
    if (existing) {
      if (Object.entries(identity).some(([key,value])=>existing[key]!==value)) {
        throw new Error('WhatsApp original send identity conflict');
      }
      return copy(existing); // Existing attempt never triggers provider send again.
    }
    const row={...identity,state:'pending',created_at:Date.now(),updated_at:Date.now()};
    this.rows.set(attempt_id,row);
    try {this.save();} catch(e){this.rows.delete(attempt_id);throw e;}
    return copy(row);
  }

  update(id,state,receipt_id='') {
    if (!states.has(state)) throw new TypeError('unknown WhatsApp state');
    const row=this.rows.get(id);
    if (!row) return null;
    if (row.state==='sent' && state!=='sent') return copy(row);
    if (row.state==='not_submitted' && state!=='not_submitted') return copy(row);
    if (state==='sent' && receipt_id!==row.message_id) throw new Error('provider message ID conflict');
    const prior={...row};
    Object.assign(row,{state,updated_at:Date.now()});
    try {this.save();} catch(e){this.rows.set(id,prior);throw e;}
    return copy(row);
  }

  acceptEvent(slot,message) {
    if (!message?.key?.fromMe || !valid(message.key.id)
        || !valid(message.key.remoteJid)) return {status:'ignored'};
    const body=message.message?.conversation || message.message?.extendedTextMessage?.text;
    if (typeof body!=='string') return {status:'unverified'};
    const candidates=[...this.rows.values()].filter(row =>
      row.slot===slot && row.message_id===message.key.id
      && row.chat_id===message.key.remoteJid && row.body_sha256===digest(body));
    if (candidates.length!==1) return {status:'not_found_or_ambiguous'};
    const row=candidates[0];
    if (row.reply_id) {
      const cited=message.message?.extendedTextMessage?.contextInfo?.stanzaId;
      if (cited!==row.reply_id) return {status:'unverified_reply'};
    }
    if (row.state==='not_submitted') return {status:'refused_conflict'};
    const match=this.update(row.attempt_id,'sent',row.message_id);
    return {status:'sent',attempt_id:match.attempt_id,message_id:match.message_id};
  }

  scopedStatus({attempt_id,user_id,account_ref,chat_id}) {
    const row=this.rows.get(attempt_id);
    if (!row || [user_id,account_ref,chat_id].some((value,i)=>value!==
      [row.user_id,row.account_ref,row.chat_id][i])) return null;
    return {attempt_id,state:row.state,message_id:row.state==='sent'?row.message_id:'',
      account_ref:row.account_ref,chat_id:row.chat_id};
  }
}
