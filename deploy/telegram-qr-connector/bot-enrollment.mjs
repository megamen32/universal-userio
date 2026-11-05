import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const TOKEN = /\b[0-9]{5,20}:[A-Za-z0-9_-]{30,80}\b/g;
export function botTokenFromInput(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input) ||
      Object.keys(input).some(k => !['token','username'].includes(k)) ||
      typeof input.token !== 'string' || !/^[0-9]{5,20}:[A-Za-z0-9_-]{30,80}$/.test(input.token) ||
      typeof input.username !== 'string' || !/^[A-Za-z][A-Za-z0-9_]{4,31}bot$/i.test(input.username))
    throw Error('invalid_bot_input');
  return {token:input.token,botId:input.token.split(':')[0],username:input.username};
}
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
// The existing connector remains the sole live client owner. Reservations are
// made before any provider IO; an unresolved earlier slot must be reconciled.
export async function enrollExplicitBot({input,principal,headers,slots,slotProfiles,inFlight,
    owners,newClient,credentials,saveToken,hasSavedToken,finishLogin,allocateSlot}) {
  const expected=botTokenFromInput(input), accountId='telegram:'+expected.botId;
  if(!principal?.can_manage || !principal.user_id)throw Error('personal_login_required');
  const matches=[...slots].filter(([id,state])=>(state.accountId||slotProfiles[id])===accountId);
  if(matches.length){
    if(matches.length!==1||!owners.allowed(matches[0][0],principal.user_id))throw Error('bot_owned_elsewhere');
    return {ok:matches[0][1].status==='connected',account_id:accountId,status:matches[0][1].status==='connected'?'already_registered':'registration_incomplete',token_returned:false};
  }
  if(hasSavedToken(principal.user_id,expected.botId))throw Error('existing_credential_requires_review');
  if(inFlight.has(accountId))throw Error('bot_enrollment_in_progress');
  inFlight.add(accountId);let client=null;
  try{
    client=newClient();await client.connect();
    const user=await client.signInBot(credentials(),{botAuthToken:expected.token});
    assertBotIdentity(user,expected);
    saveToken(principal.user_id,expected);
    const slot=allocateSlot();owners.bind(slot,principal,headers);
    slots.set(slot,{status:'connecting',mode:'bot',accountId});
    await finishLogin(slot,client,user);
    const status=slots.get(slot)?.status||'unknown';
    return {ok:status==='connected',account_id:accountId,username:expected.username,status,token_stored:true,token_returned:false};
  }finally{inFlight.delete(accountId);if(client)await client.disconnect().catch(()=>{});}
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
