// Fast unit: group authors are distinct and metadata fallback honest; max5s.
import assert from 'node:assert/strict'
import test from 'node:test'
import { messageAuthor } from '../src/message-author.ts'
const telegram = (author) => ({source:'telegram',sender:'Group',attachments:[{kind:'telegram_routing',provider_ref:JSON.stringify({group:true,group_name:'Group',...author})}]})
test('Telegram legacy name and current full name/username appear per message', () => {
  assert.equal(messageAuthor(telegram({author_name:'Анна Иванова',author_id:'1'})), 'Анна Иванова')
  assert.equal(messageAuthor(telegram({author_name:'Борис Петров',author_username:'boris',author_id:'2'})), 'Борис Петров · @boris')
})
test('unknown author does not borrow group title or another sender name', () => {
  assert.equal(messageAuthor(telegram({author_name:'Group',author_id:'3'})), 'Участник 3')
  assert.equal(messageAuthor({source:'max',sender:'4'},'group'), 'Участник 4')
})
test('MAX names are displayed in groups without changing direct chats', () => {
  const m={source:'max',sender:'5',sender_name:'Мария Сидорова · @maria'}
  assert.equal(messageAuthor(m,'group'), m.sender_name)
  assert.equal(messageAuthor(m,'direct'), '')
})
