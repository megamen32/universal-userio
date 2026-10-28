// GramJS exposes the connection class as its supported transport seam. Keep
// saved sessions/auth keys intact; pin the selected wire transport and port.
export function telegramTransportOptions(mode, ConnectionTCPObfuscated) {
  const selected = String(mode || 'full').trim().toLowerCase();
  if (selected === 'full') return {};
  if (selected !== 'obfuscated') throw new Error('Unsupported TELEGRAM_TRANSPORT');
  // GramJS 2.26.22 passes port 80 for its primary connection even when a saved
  // session has port 443. useWSS is incompatible with its proxy option, so set
  // the port on the ordinary TCP connection rather than enabling WebSockets.
  class Obfuscated443 extends ConnectionTCPObfuscated {
    constructor(options) { super({ ...options, port: 443 }); }
  }
  return { connection: Obfuscated443 };
}
