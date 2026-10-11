// Fast unit: loaded older calls survive a latest-page refresh; expected <1s/max5s.
import assert from 'node:assert/strict'
import test from 'node:test'
import { mergePhoneHistory } from '../src/phone-history.ts'

test('latest-page refresh retains older calls and updates matching entries without duplicates', () => {
  const old = [{source:'phone',message_id:'a',received_at:1,body:'start'},
    {source:'phone',message_id:'b',received_at:2,body:'transcript',seen_at:null}]
  const latest = [{...old[1],seen_at:3}, {source:'phone',message_id:'c',received_at:3,body:'ended'}]
  const merged=mergePhoneHistory(old,latest)
  assert.deepEqual(merged.map(m=>m.message_id),['a','b','c'])
  assert.equal(merged[1].seen_at,3)
  assert.equal(old[1].seen_at,null)
})
test('equal provider times retain stable page order', () => {
  const make=id=>({source:'phone',message_id:id,received_at:1})
  assert.deepEqual(mergePhoneHistory([make('a')],[make('c'),make('b')]).map(m=>m.message_id),['a','b','c'])
})
test('arrival during an older-page request preserves the boundary message as well as the new call', () => {
  const make=n=>({source:'phone',message_id:String(n).padStart(3,'0'),received_at:n})
  const initial=Array.from({length:200},(_,n)=>make(n+26))
  const poll=Array.from({length:200},(_,n)=>make(n+27))
  const retained=mergePhoneHistory(initial,poll)
  const page=Array.from({length:25},(_,n)=>make(n+1))
  const result=mergePhoneHistory(page,retained)
  assert.equal(result.length,226)
  assert.deepEqual(result.map(m=>m.message_id),Array.from({length:226},(_,n)=>make(n+1).message_id))
})
