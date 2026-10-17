import assert from 'node:assert/strict'
import test from 'node:test'
import { conversationTimeline } from '../src/conversation-drafts.ts'

test('messages and drafts share one chronological order, regardless of reply target', () => {
  const messages = [
    { message_id: 'm1', received_at: 10 },
    { message_id: 'm2', received_at: 20 },
  ]
  const anchored = { id: 'd1', status: 'proposed', created_at: 100, reply_to: { message_id: 'm1', received_at: 10 } }
  const unanchored = { id: 'd2', status: 'proposed', created_at: 15, reply_to: null }

  assert.deepEqual(conversationTimeline(messages, [anchored, unanchored]).map(entry =>
    entry.kind === 'message' ? entry.item.message_id : entry.item.id,
  ), ['m1', 'd2', 'm2', 'd1'])
})

test('draft time remains authoritative when its reply source is outside loaded history', () => {
  const messages = [{ message_id: 'm1', received_at: 20 }]
  const draft = { id: 'd1', status: 'proposed', created_at: 100, reply_to: { message_id: 'older', received_at: 10 } }
  assert.deepEqual(conversationTimeline(messages, [draft]).map(entry => entry.kind), ['message', 'draft'])
})
