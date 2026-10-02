// Minimal listen-only: connect, enable domains, wait, log everything that arrives.
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

(async () => {
  const targets = await getJSON('/json/list');
  const ext = targets.find(t => (t.url || '').startsWith(EXT_BG));
  if (!ext) { console.log('no ext'); process.exit(1); }
  console.log('ws url:', ext.webSocketDebuggerUrl);
  const ws = new WebSocket(ext.webSocketDebuggerUrl, { perMessageDeflate: false });
  let id = 0;
  ws.on('open', () => {
    console.log('OPEN');
    id = 1; ws.send(JSON.stringify({ id, method: 'Runtime.enable' }));
    id = 2; ws.send(JSON.stringify({ id, method: 'Log.enable' }));
    id = 3; ws.send(JSON.stringify({ id, method: 'Page.enable' }));
  });
  ws.on('message', (data) => {
    const m = JSON.parse(data.toString());
    if (m.id) console.log('REPLY', m.id, JSON.stringify(m.result || m.error).slice(0, 80));
    else if (m.method === 'Runtime.executionContextCreated') console.log('CTX', m.params.context.origin, m.params.context.name, 'aux:', JSON.stringify(m.params.context.auxData));
    else if (m.method === 'Runtime.consoleAPICalled') {
      const text = (m.params.args || []).map(a => a.value || a.unserializableValue || a.description || JSON.stringify(a)).join(' ');
      console.log('console.' + m.params.type + ':', text);
    }
    else if (m.method === 'Runtime.exceptionThrown') console.log('EXC', JSON.stringify(m.params.exceptionDetails).slice(0, 400));
    else if (m.method === 'Log.entryAdded') console.log('LOG', m.params.entry.level, m.params.entry.text);
    else if (m.method === 'Network.requestWillBeSent') console.log('REQ', m.params.request.url);
  });
  ws.on('error', e => console.log('ERR', e.message));
  // Also try a no-await eval that just returns a literal.
  setTimeout(() => {
    id = 99; ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression: '1+1', returnByValue: true }}));
  }, 1000);
  setTimeout(() => { ws.close(); process.exit(0); }, 6000);
})();
