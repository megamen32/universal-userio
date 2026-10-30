import assert from 'node:assert/strict';
import test from 'node:test';
import { formatTelegramMessage } from './message-format.mjs';

const entity = name => class { constructor(value) { Object.assign(this, value); this.className = name; } };
const api = {
  MessageEntityBold: entity('MessageEntityBold'),
  MessageEntityItalic: entity('MessageEntityItalic'),
  MessageEntityCode: entity('MessageEntityCode'),
  MessageEntityTextUrl: entity('MessageEntityTextUrl'),
};

test('renders bold, italic, code and a link using Telegram UTF-16 offsets', () => {
  const result = formatTelegramMessage('😀 **Жирный** и _текст_ [ссылка](https://example.com) `код`', 'telegram_markdown', api);
  assert.equal(result.text, '😀 Жирный и текст ссылка код');
  assert.deepEqual(result.entities.map(x => [x.className, x.offset, x.length]), [
    ['MessageEntityBold', 3, 6], ['MessageEntityItalic', 12, 5],
    ['MessageEntityTextUrl', 18, 6], ['MessageEntityCode', 25, 3],
  ]);
});

test('plain text and incomplete markup remain literal', () => {
  assert.deepEqual(formatTelegramMessage('**текст**', undefined, api), {text: '**текст**', entities: []});
  assert.deepEqual(formatTelegramMessage('**незакрыто', 'telegram_markdown', api), {text: '**незакрыто', entities: []});
});
