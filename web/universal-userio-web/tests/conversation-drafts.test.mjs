// Fast unit expected<1s/emergency10s: completed sends must not fabricate new messages.
import assert from 'node:assert/strict'
import test from 'node:test'
import { conversationDrafts } from '../src/conversation-drafts.ts'
const sent = { id:'draft-historic', body:'Сейчас проверю', status:'approved', created_at:1791282487,
  outbox_receipt:'telegram-qr:account-1:1981222:draft-historic' }

test('old approved draft stays out of the latest page even when its message is not loaded', () => {
  const chat={source:'telegram',account_ref:'telegram:540308572',drafts:[sent],
    messages:[{message_id:'telegram:540308572|381703703:1999999',direction:'outgoing'}]}
  assert.deepEqual(conversationDrafts(chat),[])
})
test('Telegram receipt and native message ID cannot create a duplicate completed card', () => {
  assert.deepEqual(conversationDrafts({source:'telegram',drafts:[sent],messages:[
    {message_id:'telegram:540308572|381703703:1981222',direction:'outgoing'}]}),[])
})
test('pending and UNKNOWN drafts remain actionable even when text repeats in history', () => {
  const proposed={...sent,id:'pending',status:'proposed',outbox_receipt:undefined}
  const sending={...sent,id:'unknown',status:'sending',outbox_receipt:undefined}
  const rejected={...sent,id:'rejected',status:'rejected'}
  const chat={source:'telegram',drafts:[sent,proposed,sending,rejected],messages:[]}
  assert.deepEqual(conversationDrafts(chat),[proposed,sending])
  assert.equal(chat.drafts.length,4) // selection never mutates durable records
})
