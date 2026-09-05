// Try to attach to foreground page (vk.com/im)
const WebSocket = require('ws');
const http = require('node:http');

const CDP_BASE = 'http://127.0.0.1:9223';

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
  console.log('targets:');
  for (const t of targets) console.log(' ', t.type, (t.url||'').slice(0, 80));
  const page = targets.find(t => t.type === 'page');
  if (!page) { console.log('no page'); process.exit(1); }
  const ws = new WebSocket(page.webSocketDebuggerUrl, { perMessageDeflate: false });
  let id = 0;
  ws.on('open', () => {
    console.log('OPEN');
    id = 1; ws.send(JSON.stringify({ id, method: 'Runtime.enable' }));
    id = 2; ws.send(JSON.stringify({ id, method: 'Page.enable' }));
  });
  ws.on('message', (data) => {
    const m = JSON.parse(data.toString());
    if (m.id) console.log('REPLY', m.id, JSON.stringify(m.result || m.error).slice(0, 200));
    else if (m.method === 'Runtime.executionContextCreated') console.log('CTX', m.params.context.origin, m.params.context.name);
    else if (m.method === 'Runtime.consoleAPICalled') {
      const text = (m.params.args || []).map(a => a.value || a.unserializableValue || a.description || '').join(' ');
      console.log('console.' + m.params.type + ':', text);
    }
    else if (m.method === 'Runtime.exceptionThrown') console.log('EXC', JSON.stringify(m.params.exceptionDetails).slice(0, 400));
    else if (m.method === 'Page.frameNavigated') console.log('NAV', m.params.frame.url);
  });
  ws.on('error', e => console.log('ERR', e.message));
  setTimeout(() => { id = 99; ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression: 'location.href', returnByValue: true }})); }, 1000);
  setTimeout(() => { ws.close(); process.exit(0); }, 6000);
})();
