import assert from 'node:assert/strict';
import test from 'node:test';
import {bodyAndAttachments} from '../deploy/telegram-qr-connector/transcription.mjs';

test('plain Telegram text preserves whitespace', () => {
  assert.deepEqual(bodyAndAttachments({id:1,message:'  original\n'},null),
    {body:'  original\n',attachments:[]});
});
test('captionless photos are retained as downloadable media', () => {
  const r=bodyAndAttachments({id:2,message:'',media:{className:'MessageMediaPhoto'}},null);
  assert.equal(r.body,'[Telegram photo]');
  assert.equal(r.attachments[0].kind,'photo');
  assert.equal(r.attachments[0].provider_ref,'2');
});
test('photo captions remain exact', () => {
  const r=bodyAndAttachments({id:3,message:'caption',media:{className:'MessageMediaPhoto'}},null);
  assert.equal(r.body,'caption');
  assert.equal(r.attachments[0].kind,'photo');
});
test('service messages remain visible', () => {
  const r=bodyAndAttachments({id:4,message:'',action:{className:'MessageActionPhoneCall'}},null);
  assert.equal(r.body,'[Telegram service: MessageActionPhoneCall]');
  assert.equal(r.attachments[0].kind,'service');
});
test('history voice metadata survives without a transcription call', () => {
  const r=bodyAndAttachments({id:5,message:'',media:{className:'MessageMediaDocument',
    document:{mimeType:'audio/ogg',attributes:[{className:'DocumentAttributeAudio',voice:true}]}}},null);
  assert.equal(r.body,'[Telegram voice]');
  assert.equal(r.attachments[0].kind,'voice');
  assert.equal(r.attachments[0].transcription_status,'not_requested');
});
