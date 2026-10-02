// Just collect console/runtime exceptions from extension bg page
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
  if (!ext) { console.error('no extension'); process.exit(1); }
  const ws = new WebSocket(ext.webSocketDebuggerUrl, { perMessageDeflate: false });
  let id = 0;
  const pending = new Map();
  const consoleMessages = [];
  const exceptions = [];
  ws.on('message', (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); } catch (_) { return; }
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    } else if (msg.method === 'Runtime.consoleAPICalled') {
      consoleMessages.push(msg.params);
    } else if (msg.method === 'Runtime.exceptionThrown') {
      exceptions.push(msg.params);
    } else if (msg.method === 'Log.entryAdded') {
      console.log('LOG', msg.params.entry.level, msg.params.entry.text);
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
  await call('Page.enable');
  await call('Network.enable');

  // Force reload of the extension bg page to retrigger scripts
  await call('Page.reload', { ignoreCache: true });
  await new Promise(r => setTimeout(r, 3000));

  console.log('--- console messages ---');
  for (const m of consoleMessages.slice(0, 30)) {
    const text = (m.args || []).map(a => a.value || a.description || '').join(' ');
    console.log('  ', m.type, text);
  }
  console.log('--- exceptions ---');
  for (const e of exceptions.slice(0, 10)) {
    console.log('  ', JSON.stringify(e.exceptionDetails).slice(0, 500));
  }
  ws.close();
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
