// Try to fetch lib/db.js from the extension's origin to see if scripts are accessible.
const WebSocket = require('ws');
const http = require('node:http');

const CDP_BASE = 'http://127.0.0.1:9223';
const EXT_BG = 'chrome-extension://kniehgiejgnnpgojkdhhjbgbllnfkfdk/_generated_background_page.html';

function getJSON(path) {
  return new Promise((resolve, reject) => {
    http.get(CDP_BASE + path, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

async function main() {
  const targets = await getJSON('/json/list');
  const ext = targets.find(t => (t.url || '').startsWith(EXT_BG));
  const ws = new WebSocket(ext.webSocketDebuggerUrl, { perMessageDeflate: false });
  let id = 0;
  const pending = new Map();
  const errors = [];
  const requests = [];
  ws.on('message', (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); } catch (_) { return; }
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    else if (msg.method === 'Runtime.consoleAPICalled') {
      const m = msg.params;
      const text = (m.args || []).map(a => a.value || a.description || '').join(' ');
      console.log('console.' + m.type + ':', text);
    }
    else if (msg.method === 'Runtime.exceptionThrown') {
      errors.push(msg.params.exceptionDetails);
    }
    else if (msg.method === 'Log.entryAdded') {
      console.log('LOG', msg.params.entry.level, msg.params.entry.text);
    }
    else if (msg.method === 'Network.responseReceived') {
      requests.push({ url: msg.params.response.url, status: msg.params.response.status });
    }
  });
  await new Promise(r => ws.once('open', r));
  function call(method, params) {
    id += 1;
    return new Promise((resolve) => {
      pending.set(id, resolve);
      ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); resolve({ id, timeout: true }); } }, 8000);
    });
  }
  await call('Runtime.enable');
  await call('Log.enable');
  await call('Network.enable');

  // Try fetching each background script via chrome.runtime.getURL equivalent — only fetch works in ext context.
  const r = await call('Runtime.evaluate', { expression: `(async () => {
    const out = {};
    for (const p of ['lib/db.js', 'lib/userio.js', 'lib/collect.js', 'background.js', 'manifest.json']) {
      try {
        const r = await fetch(p);
        out[p] = { status: r.status, len: (await r.text()).length };
      } catch (e) { out[p] = { error: String(e) }; }
    }
    return out;
  })()`, awaitPromise: true, returnByValue: true });
  console.log('ext fetches:', JSON.stringify(r.result && r.result.value));

  // After a brief pause, dump network and errors.
  await new Promise(r => setTimeout(r, 1500));
  console.log('--- requests ---');
  for (const q of requests) console.log('  ', q.status, q.url);
  console.log('--- exceptions ---');
  for (const e of errors) console.log('  ', JSON.stringify(e).slice(0, 500));
  ws.close();
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
