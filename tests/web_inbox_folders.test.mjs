import assert from 'node:assert/strict'
import { test } from 'node:test'
import { inInboxFolder, readInboxFolders } from '../web/universal-userio-web/src/inbox-folders.ts'

// Fast unit: folder projection/persistence guards; expected 1s, emergency 10s.
test('custom folders survive serialization and preserve cross-channel identities', () => {
  const rows = [{ id: 'clients', name: 'Клиенты', conversations: ['telegram-chat', 'mail-chat'] }]
  const restored = readInboxFolders(JSON.stringify(rows))
  assert.deepEqual(restored, rows)
  for (const [id, source] of [['telegram-chat', 'telegram'], ['mail-chat', 'gmail']]) {
    assert.equal(inInboxFolder({ id, source, unread_count: 0 }, 'clients', restored), true)
  }
  assert.equal(inInboxFolder({ id: 'other', source: 'telegram', unread_count: 3 }, 'clients', restored), false)
})

test('unread folder removes a conversation once its unread count becomes zero', () => {
  const chat = { id: 'chat', source: 'telegram', unread_count: 4 }
  assert.equal(inInboxFolder(chat, 'unread', []), true)
  assert.equal(inInboxFolder({ ...chat, unread_count: 0 }, 'unread', []), false)
})

test('personal uses actual conversation classification and excludes groups and bots', () => {
  const chat = { id: 'chat', source: 'telegram', unread_count: 0 }
  assert.equal(inInboxFolder({ ...chat, analysis_policy: { conversation_kind: 'direct' } }, 'personal', []), true)
  for (const kind of ['group', 'channel', 'telegram_bot', 'unknown']) assert.equal(inInboxFolder({ ...chat, analysis_policy: { conversation_kind: kind } }, 'personal', []), false)
})

test('corrupt browser storage cannot break the inbox or replace built-in folders', () => {
  for (const value of ['bad json', '{}', 'null']) assert.deepEqual(readInboxFolders(value), [])
  assert.deepEqual(readInboxFolders(JSON.stringify([{ id: 'all', name: 'Wrong', conversations: [] }, { id: 'x', name: 'Bad', conversations: [3] }])), [])
})

test('duplicate folder and conversation IDs do not create duplicate UI rows', () => {
  const row = { id: 'custom', name: 'Моя папка', conversations: ['x', 'x'] }
  assert.deepEqual(readInboxFolders(JSON.stringify([row, row])), [{ ...row, conversations: ['x'] }])
})

test('a deleted folder never silently expands into the entire inbox', () => {
  assert.equal(inInboxFolder({ id: 'x', source: 'gmail', unread_count: 1 }, 'deleted', []), false)
})
