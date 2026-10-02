// Raw CDP probe over WS — talks to BrowserOS on 9223 directly.
// 1. attaches to the UserIO extension background page (our ID)
// 2. evaluates JS in that context
// 3. navigates the only foreground page to vk.com/im
// 4. waits for messages and prints capture state
import WebSocket from 'ws';
import http from 'node:http';

const CDP_BASE = 'http://127.0.0.1:9223';
const EXT_BG = 'chrome-extension://kniehgiejgnnpgojkdhhjbgbllnfkfdk/_generated_background_page.html';

function getJSON(path) {
  return new Promise((resolve, reject) => {
    http.get(CDP_BASE + path, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    this.ready = new Promise((resolve, reject) => {
      this.ws.on('open', resolve);
      this.ws.on('error', reject);
    });
    this.ws.on('message', (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          if (msg.error) reject(new Error(JSON.stringify(msg.error))); else resolve(msg.result);
        } else if (msg.method) {
          this.events.push(msg);
        }
      } catch (_) {}
    });
  }
  send(method, params = {}) {
    this.id += 1;
    const id = this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  close() { this.ws.close(); }
}

async function main() {
  const targets = await getJSON('/json/list');
  const ext = targets.find(t => (t.url || '').startsWith(EXT_BG));
  if (!ext) {
    console.error('extension background not found');
    process.exit(1);
  }
  console.log('extension ws:', ext.webSocketDebuggerUrl);
  const page = targets.find(t => t.type === 'page');
  console.log('foreground page:', page && page.url);

  const cdp = new CDP(ext.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Runtime.enable');
  // Talk to the extension's globals
  const probe = await cdp.send('Runtime.evaluate', {
    expression: `JSON.stringify({
      hasDB: !!self.UserIODB,
      hasUSERIO: !!self.UserIO,
      hasCollect: !!self.Collect,
      dbStats: (async () => self.UserIODB && await self.UserIODB.stats())(),
    })`,
    awaitPromise: true,
    returnByValue: true,
  });
  console.log('extension probe:', probe.result.value);

  const foregroundWsUrl = page.webSocketDebuggerUrl;
  const pageCdp = new CDP(foregroundWsUrl);
  await pageCdp.ready;
  await pageCdp.send('Page.enable');
  await pageCdp.send('Runtime.enable');

  // Navigate to vk.com/im
  await pageCdp.send('Page.navigate', { url: 'https://vk.com/im' });
  await new Promise(r => setTimeout(r, 5000));

  // Read current URL
  const nav1 = await pageCdp.send('Runtime.evaluate', { expression: 'location.href', returnByValue: true });
  console.log('after nav1:', nav1.result.value);

  if (nav1.result.value.includes('id.vk.ru') || nav1.result.value.includes('login')) {
    console.log('VK is on login — session not live in this BrowserOS profile.');
  }

  // Walk into background page console events
  console.log('captured events on extension bg page:', cdp.events.length);
  for (const e of cdp.events.slice(0, 10)) console.log('  ', e.method, e.params && e.params.type);
  pageCdp.close();
  cdp.close();
}

main().catch(e => { console.error(e); process.exit(1); });
