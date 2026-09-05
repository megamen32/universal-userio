// Inject VK cookies via CDP Network.setCookie, then drive vk.com/im and watch capture.
const WebSocket = require('ws');
const http = require('node:http');
const fs = require('node:fs');

const CDP_BASE = 'http://127.0.0.1:9224';
const VK_COOKIES_FILE = process.argv[2] || '/tmp/vk_cookies.json';
const VK_LS_FILE = process.argv[3] || '/tmp/vk_localstorage.json';
const VK_SS_FILE = process.argv[4] || '/tmp/vk_sessionstorage.json';

function getJSON(path) {
  return new Promise((resolve, reject) => {
    http.get(CDP_BASE + path, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

class CDP {
  constructor(wsUrl, label = 'cdp') {
    this.label = label;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    this.ws = new WebSocket(wsUrl, { perMessageDeflate: false });
    this.ready = new Promise((resolve, reject) => {
      this.ws.once('open', resolve);
      this.ws.once('error', reject);
      setTimeout(() => reject(new Error('open timeout 5s')), 5000);
    });
    this.ws.on('message', (data) => {
      let msg;
      try { msg = JSON.parse(data.toString()); } catch (_) { return; }
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error))); else resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }
  send(method, params = {}) {
    this.id += 1;
    const id = this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); reject(new Error(`${method} timeout 12s`)); } }, 12000);
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
  const cookies = JSON.parse(fs.readFileSync(VK_COOKIES_FILE, 'utf8'));
  if (!Array.isArray(cookies)) throw new Error('expected array of cookies');
  console.log('loaded', cookies.length, 'cookies from', VK_COOKIES_FILE);

  const targets = await getJSON('/json/list');
  console.log('targets:');
  for (const t of targets) console.log(' ', t.type, (t.url||'').slice(0, 90));

  // Use the foreground page to set cookies.
  const page = targets.find(t => t.type === 'page');
  if (!page) throw new Error('no page target');
  const ws = new CDP(page.webSocketDebuggerUrl, 'page');
  await ws.ready;
  await ws.send('Network.enable');
  await ws.send('Runtime.enable');
  await ws.send('Storage.setCookies', { cookies: [] }); // ensure domain enable
  // Actually: Network.setCookie via domain-qualified.
  let ok = 0, fail = 0;
  for (const c of cookies) {
    const domain = c.domain && c.domain.startsWith('.') ? c.domain : ('.' + (c.domain || 'vk.ru'));
    const params = {
      name: c.name,
      value: c.value,
      domain: domain,
      path: c.path || '/',
      secure: !!c.secure,
      httpOnly: !!c.httpOnly,
      sameSite: (c.sameSite === 'no_restriction') ? 'None' : (c.sameSite || 'Lax'),
    };
    if (c.expirationDate) params.expires = Math.floor(c.expirationDate);
    try {
      const r = await ws.send('Network.setCookie', params);
      if (r && r.success) ok += 1;
      else fail += 1;
    } catch (e) {
      console.log('setCookie failed', c.name, e.message);
      fail += 1;
    }
  }
  console.log('setCookie ok/fail:', ok, '/', fail);

  // Also set localStorage on vk.ru origin: enable DOM first.
  const ls = JSON.parse(fs.readFileSync(VK_LS_FILE, 'utf8'));
  console.log('loaded', ls.length, 'localStorage entries');

  const ss = JSON.parse(fs.readFileSync(VK_SS_FILE, 'utf8'));
  console.log('loaded', ss.length, 'sessionStorage entries');

  // Navigate to vk.ru first to set localStorage on the right origin.
  await ws.send('Page.enable');
  await ws.send('Page.navigate', { url: 'https://vk.ru/' });
  await new Promise(r => setTimeout(r, 4000));

  for (const entry of ls) {
    await ws.eval(`(() => { try { localStorage.setItem(${JSON.stringify(entry.key)}, ${JSON.stringify(entry.value)}); return 'ok'; } catch (e) { return String(e); } })()`);
  }
  for (const entry of ss) {
    await ws.eval(`(() => { try { sessionStorage.setItem(${JSON.stringify(entry.key)}, ${JSON.stringify(entry.value)}); return 'ok'; } catch (e) { return String(e); } })()`);
  }
  console.log('localStorage + sessionStorage set');

  // Reload to apply.
  await ws.send('Page.navigate', { url: 'https://vk.ru/' });
  await new Promise(r => setTimeout(r, 5000));
  const nav1 = await ws.eval('location.href');
  console.log('after vk.ru nav:', nav1);

  // Check user login via API
  const apiResp = await ws.eval(`(async () => {
    try {
      const r = await fetch('https://vk.ru/al_im.php', { credentials: 'include' });
      const t = await r.text();
      return { status: r.status, len: t.length, head: t.slice(0, 300) };
    } catch (e) { return { error: String(e) }; }
  })()`, true);
  console.log('al_im.php:', JSON.stringify(apiResp));

  // Navigate to vk.com/im
  await ws.send('Page.navigate', { url: 'https://vk.com/im' });
  await new Promise(r => setTimeout(r, 6000));
  const nav2 = await ws.eval('location.href');
  console.log('after vk.com/im:', nav2);

  // Inspect page DOM for messages
  const peerId = await ws.eval(`(() => {
    const m = location.search.match(/[?&]sel=([^&]+)/);
    return m ? m[1] : null;
  })()`);
  console.log('sel=', peerId);

  // Wait for messages to render, then walk DOM
  await new Promise(r => setTimeout(r, 5000));
  const messages = await ws.eval(`(function () {
    const out = [];
    const nodes = document.querySelectorAll('[data-msgid], [data-message-id], .im-mess');
    for (const n of nodes) {
      out.push({
        msgid: n.getAttribute('data-msgid') || n.getAttribute('data-message-id'),
        cls: (n.className || '').slice(0, 80),
        text: (n.innerText || '').slice(0, 200),
        imgs: [...n.querySelectorAll('img')].slice(0, 5).map(i => ({
          src: (i.currentSrc || i.src || '').slice(0, 200),
          alt: (i.alt || '').slice(0, 50),
          cls: (i.className || '').slice(0, 80),
        })),
        videos: [...n.querySelectorAll('video')].slice(0, 3).map(v => (v.currentSrc || v.src || '').slice(0, 200)),
        audios: [...n.querySelectorAll('audio')].slice(0, 3).map(v => (v.currentSrc || v.src || '').slice(0, 200)),
        docs: [...n.querySelectorAll('a')].slice(0, 3).map(a => (a.href || '').slice(0, 200)),
      });
    }
    return out;
  })()`);
  console.log('messages found:', messages.length);
  for (const m of messages.slice(0, 5)) {
    console.log('  msgid:', m.msgid);
    console.log('  cls:', m.cls);
    console.log('  text:', m.text.slice(0, 120));
    if (m.imgs.length) console.log('  imgs:', JSON.stringify(m.imgs).slice(0, 200));
    if (m.videos.length) console.log('  videos:', JSON.stringify(m.videos));
    if (m.audios.length) console.log('  audios:', JSON.stringify(m.audios));
    if (m.docs.length) console.log('  docs:', JSON.stringify(m.docs));
  }
  ws.close();
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
