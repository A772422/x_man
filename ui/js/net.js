// REST + WebSocket client. The token is injected into the page by the server at load time.
const TOKEN = document.querySelector('meta[name="mrx-token"]')?.content || '';

export async function api(path, { method = 'GET', body, raw = false } = {}) {
  const r = await fetch(path, { method, headers: { 'x-mrx-token': TOKEN, ...(body ? { 'content-type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  if (raw) return r;
  let data = null; try { data = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) { const e = new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`); e.status = r.status; e.data = data; throw e; }
  return data;
}
export async function blobUrl(path) { const r = await api(path, { raw: true }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return URL.createObjectURL(await r.blob()); }
export const tool = (name, args = {}) => api(`/api/tools/${name}`, { method: 'POST', body: { args } }).then((d) => d.result);

export class Events {
  constructor(onEvent, onStatus) { this.onEvent = onEvent; this.onStatus = onStatus; this.ws = null; this.retry = 0; this.queue = []; this.connect(); }
  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws/events?token=${encodeURIComponent(TOKEN)}`);
    this.ws.onopen = () => { this.retry = 0; this.onStatus(true); for (const m of this.queue.splice(0)) this.ws.send(m); };
    this.ws.onmessage = (e) => { try { this.onEvent(JSON.parse(e.data)); } catch (err) { console.error('bad event', err); } };
    this.ws.onclose = () => { this.onStatus(false); setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.retry++, 8000)); };
    this.ws.onerror = () => this.ws.close();
  }
  send(obj) { const s = JSON.stringify(obj); if (this.ws && this.ws.readyState === 1) this.ws.send(s); else this.queue.push(s); }
}
