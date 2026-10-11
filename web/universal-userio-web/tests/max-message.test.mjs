// Fast unit: URL previews remain editable, uploaded media does not; expected <1s/max5s.
import assert from 'node:assert/strict'
import test from 'node:test'
import { canEditMaxText, providerPeerLabel } from '../src/max-message.ts'

test('a MAX URL preview must not hide the edit action', () => {
  assert.equal(canEditMaxText([{kind:'share', filename:'Example Domain'}]), true)
  assert.equal(canEditMaxText([]), true)
})
test('editing a caption must not silently discard a photo or sticker', () => {
  assert.equal(canEditMaxText([{kind:'share'}, {kind:'image'}]), false)
  assert.equal(canEditMaxText([{kind:'sticker'}]), false)
})
test('a numeric MAX contact is not labelled as a Telegram contact', () => {
  assert.equal(providerPeerLabel('900000104','max'), 'MAX:900000104')
  assert.equal(providerPeerLabel('900000300','telegram'), 'tg:900000300')
})
