import test from 'node:test';
import assert from 'node:assert/strict';
import { mirrorMetadata, confirmedDeletedIds } from './mirror-metadata.mjs';

test('native direction/date and separate inbox/outbox watermarks', () => {
  const dialog = {readInboxMaxId: 12, readOutboxMaxId: 9};
  assert.deepEqual(mirrorMetadata({id: 10, date: 123, out: true}, dialog),
    {direction:'outgoing', received_at:123, provider_read:false});
  assert.deepEqual(mirrorMetadata({id: 10, date: 123}, dialog),
    {direction:'incoming', received_at:123, provider_read:true});
  assert.deepEqual(mirrorMetadata({id: 10, date: 123}, null), {direction:'incoming', received_at:123});
});
test('exact ID probe, never limited history absence, confirms deletion', () => {
  assert.deepEqual(confirmedDeletedIds([10,11], [{id:10,date:123}, {id:11,className:'MessageEmpty'}]), [11]);
  assert.deepEqual(confirmedDeletedIds([10,11], [{id:10,date:123}]), []);
  assert.deepEqual(confirmedDeletedIds([10,11], [undefined,undefined]), []);
});

test('interlocutor profile comes from chat, including outgoing messages', () => {
  const result = mirrorMetadata({ out: true, chat: { username: 'semerindima', firstName: 'Дмитрий', lastName: 'Семерин', phone: '79841303184' }, sender: { username: 'self' } });
  assert.deepEqual(result.peer_profile, { username: 'semerindima', first_name: 'Дмитрий', last_name: 'Семерин', phone: '79841303184' });
  assert.equal(mirrorMetadata({chat: {min: true, username:'partial'}}).peer_profile, undefined);
});
