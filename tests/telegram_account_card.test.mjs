import test from 'node:test';
import assert from 'node:assert/strict';
import { telegramAccountCard } from '../deploy/telegram-qr-connector/account-card.mjs';

test('revoked Telegram session is shown as disconnected and can be removed', () => {
  const card = telegramAccountCard({
    id: 'account-7', accountId: 'telegram:123456789', status: 'connected', sync: 'relogin-required',
  }, { id: 'telegram:123456789', display_name: 'Никита Розанов', can_disconnect: true });

  assert.equal(card.title, 'Никита Розанов · Telegram · 56789');
  assert.equal(card.status, 'Сессия отключена · Telegram отозвал вход');
  assert.match(card.note, /не подключена/);
  assert.equal(card.action.label, 'Убрать из UserIO');
  assert.equal(card.action.accountId, 'telegram:123456789');
});

test('active Telegram session is clearly identified and has a disconnect action', () => {
  const card = telegramAccountCard({
    id: 'account-8', accountId: 'telegram:987654321', status: 'connected', sync: 'on',
  }, { id: 'telegram:987654321', display_name: 'Секретарь Никиты', can_disconnect: true });

  assert.equal(card.title, 'Секретарь Никиты · Telegram · 54321');
  assert.equal(card.status, 'Аккаунт подключён');
  assert.equal(card.action.label, 'Отключить аккаунт');
});

test('non-revocable or incomplete login slots do not show a destructive action', () => {
  const card = telegramAccountCard({ id: 'account-9', status: 'waiting' }, null);
  assert.equal(card.title, 'Сохранённая сессия Telegram №9');
  assert.equal(card.action, null);
});

test('server-managed Telegram accounts do not get an unsafe QR-page action', () => {
  const card = telegramAccountCard({
    id: 'account-10', accountId: 'telegram:987650000', status: 'connected', sync: 'relogin-required',
  }, { id: 'telegram:987650000', display_name: 'Общий аккаунт', can_disconnect: false });
  assert.equal(card.action, null);
});

test('orphaned revoked QR slots can be removed without pretending they are connected', () => {
  const card = telegramAccountCard({
    id: 'account-11', accountId: 'telegram:456789012', status: 'connected', sync: 'relogin-required',
  }, null);
  assert.equal(card.status, 'Сессия отключена · Telegram отозвал вход');
  assert.match(card.note, /Имя аккаунта не сохранено/);
  assert.equal(card.action.kind, 'revoked-slot');
  assert.equal(card.action.slot, 'account-11');
});
