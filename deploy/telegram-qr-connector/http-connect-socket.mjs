// Same normal GramJS networkSocket seam as X-manager's http-connect-socket.ts.
// A tunnel changes transport only; the existing client retains its auth/session.
import { Socket } from 'node:net';

export function httpConnectOptions(value, PromisedNetSockets) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !url.hostname || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('TELEGRAM_HTTP_PROXY_URL requires an HTTP proxy root URL');
  }
  const port = Number(url.port || 80);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error('invalid HTTP proxy port');
  const proxy = { host: url.hostname, port };
  const auth = url.username || url.password
    ? 'Proxy-Authorization: Basic ' + Buffer.from(decodeURIComponent(url.username) + ':' + decodeURIComponent(url.password)).toString('base64') + '\r\n'
    : '';
  class HttpConnectSockets extends PromisedNetSockets {
    constructor() { super(undefined); }
    async connect(targetPort, targetIP) {
      const tunnel = new Socket();
      tunnel.on('error', () => {});
      let timer;
      try {
        const remaining = await Promise.race([
          new Promise((_, reject) => { timer = setTimeout(() => { tunnel.destroy(); reject(new Error('HTTP proxy CONNECT timed out')); }, 15000); }),
          new Promise((resolve, reject) => {
            let bytes = Buffer.alloc(0);
            const cleanup = () => { tunnel.off('data', data); tunnel.off('error', error); tunnel.off('end', ended); };
            const error = () => { cleanup(); reject(new Error('HTTP proxy CONNECT unavailable')); };
            const ended = () => { cleanup(); reject(new Error('HTTP proxy CONNECT closed')); };
            const data = chunk => {
              bytes = Buffer.concat([bytes, chunk]);
              const end = bytes.indexOf('\r\n\r\n');
              if (end < 0) { if (bytes.length > 32768) error(); return; }
              cleanup();
              if (!/^HTTP\/\d\.\d 200(?:\s|$)/.test(bytes.subarray(0, end).toString('latin1').split('\r\n')[0])) {
                reject(new Error('HTTP proxy CONNECT refused')); return;
              }
              resolve(bytes.subarray(end + 4));
            };
            tunnel.on('data', data); tunnel.once('error', error); tunnel.once('end', ended);
            tunnel.connect(proxy.port, proxy.host, () => {
              const target = targetIP + ':' + targetPort;
              tunnel.write('CONNECT ' + target + ' HTTP/1.1\r\nHost: ' + target + '\r\n' + auth + '\r\n');
            });
          }),
        ]);
        clearTimeout(timer);
        this.client = tunnel; this.stream = remaining; this.closed = false;
        this.canRead = new Promise(resolve => { this.resolveRead = resolve; });
        await this.receive();
        if (remaining.length) this.resolveRead(true);
        tunnel.on('close', () => { this.closed = true; if (this.resolveRead) this.resolveRead(false); });
        return this;
      } catch (error) { clearTimeout(timer); tunnel.destroy(); throw error; }
    }
  }
  return { proxy, networkSocket: HttpConnectSockets };
}
