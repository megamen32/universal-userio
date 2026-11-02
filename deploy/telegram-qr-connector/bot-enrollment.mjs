import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const TOKEN = /\b[0-9]{5,20}:[A-Za-z0-9_-]{30,80}\b/g;
export function redactBotFatherToken(text) {
  return String(text || '').replace(TOKEN, '[BOT_TOKEN_REDACTED]');
}
export function botTokenFromMessage(message, botFather, expectedUsername) {
  if (!botFather?.bot || !botFather?.verified || String(botFather.username || '').toLowerCase() !== 'botfather') throw Error('verified_botfather_required');
  if (message?.out || String(message?.senderId || '') !== String(botFather.id)) throw Error('botfather_inbound_required');
  const text = String(message.message || '');
  const tokens = text.match(TOKEN) || [];
  if (tokens.length !== 1) throw Error('one_bot_token_required');
  const username = String(expectedUsername || '').replace(/^@/, '');
  if (!/^[A-Za-z][A-Za-z0-9_]{4,31}bot$/i.test(username)) throw Error('valid_expected_bot_username_required');
  return { token: tokens[0], botId: tokens[0].split(':')[0], username };
}
export function assertBotIdentity(user, expected) {
  if (!user?.bot || String(user.id) !== expected.botId || String(user.username || '').toLowerCase() !== expected.username.toLowerCase()) throw Error('bot_identity_mismatch');
}
export function botCredentialPath(root, owner, botId) {
  if (!owner || !/^[0-9]{5,20}$/.test(botId)) throw Error('credential_scope_required');
  return root + '/' + createHash('sha256').update(owner).digest('hex').slice(0,24) + '-bot-' + botId + '.age';
}
export function saveEncryptedBotToken(root, keyPath, owner, expected) {
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const path = botCredentialPath(root, owner, expected.botId);
  if (existsSync(path)) throw Error('bot_credential_already_exists_no_rotation');
  const key = spawnSync('age-keygen', ['-y', keyPath], { encoding: 'utf8' });
  if (key.status !== 0 || !key.stdout.trim()) throw Error('bot_credential_recipient_unavailable');
  const encrypted = spawnSync('age', ['--encrypt', '--recipient', key.stdout.trim()], { input: expected.token, maxBuffer: 65536 });
  if (encrypted.status !== 0 || !encrypted.stdout.length) throw Error('bot_credential_encryption_failed');
  const temp = path + '.next';
  writeFileSync(temp, encrypted.stdout, { mode: 0o600, flag: 'wx' });
  renameSync(temp, path);
  return path;
}
