// Fast unit, expected1/max10s. Real installed SDK socket seam + bounded TCP
// fixture; no Telegram account, provider message, key or auth session.
import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer} from 'node:net';
import {createRequire} from 'node:module';
import {httpConnectOptions} from './http-connect-socket.mjs';
const require = createRequire('/opt/userio-telegram-qr/package.json');
const {PromisedNetSockets} = require('telegram/extensions/PromisedNetSockets.js');

test('HTTP tunnel preserves coalesced MTProto bytes on actual GramJS socket', {timeout:2000}, async () => {
  let request = '';
  const server = createServer(socket => socket.once('data', data => {
    request = data.toString(); socket.write(Buffer.concat([Buffer.from('HTTP/1.1 200 Connection established\r\n\r\n'), Buffer.from([1,2,3,4])]));
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const options = httpConnectOptions('http://127.0.0.1:' + server.address().port, PromisedNetSockets);
  const socket = new options.networkSocket(options.proxy);
  try {
    await socket.connect(80, '149.154.167.41');
    assert.equal(request, 'CONNECT 149.154.167.41:80 HTTP/1.1\r\nHost: 149.154.167.41:80\r\n\r\n');
    assert.deepEqual(await socket.readExactly(4), Buffer.from([1,2,3,4]));
  } finally { await socket.close(); await new Promise(resolve => server.close(resolve)); }
});

test('refused HTTP tunnel never becomes a direct connection', {timeout:2000}, async () => {
  const server = createServer(socket => socket.once('data', () => socket.end('HTTP/1.1 403 Forbidden\r\n\r\n')));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const options = httpConnectOptions('http://127.0.0.1:' + server.address().port, PromisedNetSockets);
  try { await assert.rejects(new options.networkSocket().connect(80, '149.154.167.41'), /refused/); }
  finally { await new Promise(resolve => server.close(resolve)); }
});

test('HTTP option rejects unsupported schemes and preserves declared root', () => {
  assert.throws(() => httpConnectOptions('https://proxy.example', PromisedNetSockets));
  assert.throws(() => httpConnectOptions('http://proxy.example/path', PromisedNetSockets));
  assert.equal(httpConnectOptions('http://proxy.example:3128', PromisedNetSockets).proxy.port, 3128);
});
