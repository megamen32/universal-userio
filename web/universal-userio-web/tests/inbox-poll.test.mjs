// Fast unit: delayed reads must not overlap or survive tab cleanup; max5s.
import assert from 'node:assert/strict'
import test from 'node:test'
import { startInboxPolling } from '../src/inbox-poll.ts'

test('poll waits for the current read and does not reschedule after cleanup', async () => {
  let scheduled = [], finish, reads=0, cancelled=[]
  const stop=startInboxPolling(() => { reads++; return new Promise(r=>finish=r) },
    ()=>true, fn=>{scheduled.push(fn);return scheduled.length}, id=>cancelled.push(id))
  scheduled.shift()()
  assert.equal(reads,1)
  assert.equal(scheduled.length,0)
  stop();finish();await new Promise(r=>setImmediate(r))
  assert.equal(scheduled.length,0)
  assert.equal(cancelled.length,1)
})
test('hidden tabs skip reads and a failed read does not stop future refreshes', async () => {
  let callbacks=[], visible=false, reads=0
  const stop=startInboxPolling(async()=>{reads++;throw Error('read unavailable')},
    ()=>visible, fn=>{callbacks.push(fn);return callbacks.length}, ()=>{})
  callbacks.shift()();await new Promise(r=>setImmediate(r))
  assert.equal(reads,0)
  visible=true;callbacks.shift()();await new Promise(r=>setImmediate(r))
  assert.equal(reads,1);assert.equal(callbacks.length,1);stop()
})

test('switching chats while an old read is pending does not create overlapping poll cycles', async () => {
  const busy={current:false};let pending=[], finish, active=0, peak=0
  const schedule=fn=>{pending.push(fn);return pending.length}
  const first=startInboxPolling(()=>{active++;peak=Math.max(peak,active);
    return new Promise(r=>{finish=()=>{active--;r()}})},()=>true,schedule,()=>{},busy)
  pending.shift()();first()
  const second=startInboxPolling(async()=>{active++;peak=Math.max(peak,active);active--},
    ()=>true,schedule,()=>{},busy)
  pending.shift()();await new Promise(r=>setImmediate(r))
  assert.equal(peak,1);assert.equal(active,1)
  finish();await new Promise(r=>setImmediate(r))
  pending.shift()();await new Promise(r=>setImmediate(r))
  assert.equal(peak,1);assert.equal(active,0);second()
})
