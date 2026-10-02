// Verbose CDP probe with step-by-step logging
const WebSocket = require('ws');
const http = require('node:http');

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
  constructor(wsUrl, label = 'cdp') {
    this.label = label;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    console.log(`[${label}] connecting to ${wsUrl}`);
    this.ws = new WebSocket(wsUrl, { perMessageDeflate: false });
    this.ws.on('open', () => console.log(`[${label}] open`));
    this.ws.on('error', (e) => console.log(`[${label}] error`, e.message));
    this.ws.on('close', () => console.log(`[${label}] close`));
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
    this.ready = new Promise((resolve, reject) => {
      this.ws.on('open', resolve);
      this.ws.on('error', reject);
      setTimeout(() => reject(new Error('open timeout 5s')), 5000);
    });
  }
  send(method, params = {}) {
    this.id += 1;
    const id = this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} timeout 10s`));
        }
      }, 10000);
    });
  }
  async eval(expression, awaitPromise = false) {
    const r = await this.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

async function main() {
  console.log('targets:', (await getJSON('/json/list')).length);
  const targets = await getJSON('/json/list');
  for (const t of targets) console.log(' ', t.type, t.url);
  const ext = targets.find(t => (t.url || '').startsWith(EXT_BG));
  if (!ext) {
    console.error('extension background not found');
    process.exit(1);
  }
  const page = targets.find(t => t.type === 'page');
  console.log('ext ws:', ext.webSocketDebuggerUrl);

  const extCdp = new CDP(ext.webSocketDebuggerUrl, 'ext');
  await extCdp.ready;
  await extCdp.send('Runtime.enable');
  const probe = await extCdp.eval(`JSON.stringify({
    hasDB: typeof self.UserIODB,
    hasUSERIO: typeof self.UserIO,
    hasCollect: typeof self.Collect,
  })`);
  console.log('extension probe:', probe);

  console.log('page ws:', page.webSocketDebuggerUrl);
  const pageCdp = new CDP(page.webSocketDebuggerUrl, 'page');
  await pageCdp.ready;
  await pageCdp.send('Page.enable');
  await pageCdp.send('Runtime.enable');

  console.log('navigating ...');
  await pageCdp.send('Page.navigate', { url: 'https://vk.com/im' });
  await new Promise(r => setTimeout(r, 6000));
  const nav1 = await pageCdp.eval('location.href');
  console.log('after nav1:', nav1);

  if (nav1.includes('id.vk.ru') || nav1.includes('login')) {
    await pageCdp.send('Page.navigate', { url: 'https://vk.ru/im' });
    await new Promise(r => setTimeout(r, 6000));
    const nav2 = await pageCdp.eval('location.href');
    console.log('after nav2:', nav2);
  }

  const cookies = await pageCdp.eval(`document.cookie`);
  console.log('cookies:', cookies && cookies.slice(0, 200));

  const apiResp = await pageCdp.eval(`(async () => {
    try {
      const r = await fetch('https://vk.com/al_im.php', { credentials: 'include' });
      const t = await r.text();
      return { status: r.status, len: t.length, head: t.slice(0, 200) };
    } catch (e) { return { error: String(e) }; }
  })()`, true);
  console.log('al_im.php:', JSON.stringify(apiResp));

  pageCdp.close();
  extCdp.close();
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
