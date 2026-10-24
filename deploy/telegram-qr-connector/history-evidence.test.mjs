import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { historyEvidence } from './history-evidence.mjs';

test('fresh native UTF-8 text and direction are fingerprinted without plaintext', () => {
  const result = historyEvidence({id:42, date:1791991348, out:true, message:'Точный текст'});
  assert.equal(result.body_sha256, createHash('sha256').update('Точный текст').digest('hex'));
  assert.equal(result.outgoing, true);
  assert.equal(result.has_attachments, false);
  assert.equal('body' in result, false);
});
test('actual media, edits, reply context and other senders remain distinguishable', () => {
  const result = historyEvidence({id:43, date:1791991348, out:false, message:'текст',
    media:{className:'MessageMediaDocument'}, editDate:1791991350, replyTo:{replyToMsgId:41}});
  assert.equal(result.outgoing, false);
  assert.equal(result.has_attachments, true);
  assert.equal(result.edited, true);
  assert.equal(result.has_reply_context, true);
});
test('routing metadata and a link preview are not payload attachments', () => {
  assert.equal(historyEvidence({id:1, date:1, message:'link', media:{className:'MessageMediaWebPage'}},
    [{kind:'telegram_routing'}, {kind:'webpage'}]).has_attachments, false);
  assert.equal(historyEvidence({id:1, date:1, message:'caption'}, [{kind:'photo'}]).has_attachments, true);
});
