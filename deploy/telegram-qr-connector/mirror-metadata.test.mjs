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
