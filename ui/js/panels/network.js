import { h, clear, ago, fmtDateTime, put } from '../dom.js';
import { api } from '../net.js';

const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}, ...kids) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); kids.forEach((k) => e.append(k)); return e; };
const COLORS = { Router: '#fbbf24', Printer: '#8b7bff', TV: '#ff8fd0', Camera: '#ff5d6c', 'IoT device': '#4ade80', Unknown: '#7d93aa' };
const glyph = (t) => (/Router/.test(t) ? '⌁' : /Printer/.test(t) ? '⎙' : /TV/.test(t) ? '▭' : /Phone|Tablet/.test(t) ? '▯' : /PC|Laptop/.test(t) ? '▣' : /Camera/.test(t) ? '◉' : /IoT/.test(t) ? '◈' : '?');
const colorOf = (t) => COLORS[t] || (/PC|Laptop/.test(t) ? '#3ee6ff' : /Phone|Tablet/.test(t) ? '#4d9bff' : COLORS.Unknown);

export function create(ctx) {
  const root = ctx.root; let devs = []; let meta = null; let known = []; let names = {}; let tip = null;
  async function load() { try { const d = await api('/api/network/devices'); devs = d.last.devices || []; meta = d.last; known = d.known; names = d.names || {}; } catch (e) { ctx.toast(e.message, 'err'); } render(); }
  async function scan() { try { await api('/api/network/scan', { method: 'POST' }); } catch (e) { ctx.toast(e.message, 'err'); } }
  function radar() {
    const svg = el('svg', { viewBox: '0 0 500 500', role: 'img', 'aria-label': 'Network radar' });
    [70, 130, 190, 240].forEach((r) => svg.append(el('circle', { cx: 250, cy: 250, r, fill: 'none', stroke: '#1a2a3d', 'stroke-width': 1 })));
    svg.append(el('line', { x1: 250, y1: 10, x2: 250, y2: 490, stroke: '#12202f' }), el('line', { x1: 10, y1: 250, x2: 490, y2: 250, stroke: '#12202f' }));
    if (ctx.state.scan.running) svg.append(el('g', { class: 'sweep' }, el('path', { d: 'M250 250 L250 10 A240 240 0 0 1 420 80 Z', fill: 'rgba(62,230,255,.14)' })));
    const gw = devs.find((d) => d.is_gateway); const self = devs.find((d) => d.is_self); const others = devs.filter((d) => d !== gw);
    const center = gw || self; const ring = others.filter((d) => d !== center);
    const pos = new Map(); if (center) pos.set(center, [250, 250]);
    ring.forEach((d, i) => { const n = ring.length; const layer = n > 10 ? (i % 2 ? 195 : 130) : 160; const a = (i / n) * Math.PI * 2 - Math.PI / 2; pos.set(d, [250 + Math.cos(a) * layer, 250 + Math.sin(a) * layer]); });
    if (center) ring.forEach((d) => { const [x, y] = pos.get(d); svg.append(el('line', { x1: 250, y1: 250, x2: x, y2: y, stroke: '#1d3a52', 'stroke-width': 1.2 })); });
    pos.forEach(([x, y], d) => {
      const c = colorOf(d.device_type); const g = el('g', { class: 'node', tabindex: 0 });
      g.append(el('circle', { class: 'n', cx: x, cy: y, r: d === center ? 22 : 16, fill: '#0b1622', stroke: c, 'stroke-width': 2 }), el('text', { x, y: y + 5, 'text-anchor': 'middle', style: `fill:${c};font-size:15px` }, glyph(d.device_type)),
        el('text', { x, y: y + (d === center ? 38 : 31), 'text-anchor': 'middle' }, (names[d.mac || d.ip] || d.name || d.ip).slice(0, 18)));
      g.addEventListener('mouseenter', (e) => showTip(d, x, y)); g.addEventListener('mouseleave', hideTip); g.addEventListener('focus', () => showTip(d, x, y)); g.addEventListener('blur', hideTip);
      svg.append(g);
    });
    return svg;
  }
  function showTip(d, x, y) { if (!tip) return; tip.style.display = 'block'; tip.style.left = `${x / 5}%`; tip.style.top = `${y / 5 + 4}%`; tip.replaceChildren(h('b', {}, d.name || d.ip), h('div', {}, `${d.ip}${d.mac ? ' · ' + d.mac : ''}`), h('div', { class: 'muted' }, `${d.device_type} · via ${d.methods.join(', ')}`)); }
  function hideTip() { if (tip) tip.style.display = 'none'; }
  function render() {
    clear(root); const sc = ctx.state.scan; tip = h('div', { class: 'radar-tip' });
    put(root, h('h2', {}, 'Network radar'), h('p', { class: 'sub' }, 'Discovers devices on the local network(s) this computer is connected to, using ARP/neighbour tables, ICMP echo on your own subnet, SSDP and mDNS. Only scan networks you administer. Nothing is port-scanned.'),
      h('div', { class: 'row', style: 'margin-bottom:10px' }, h('button', { class: 'btn primary', disabled: sc.running, onclick: scan }, sc.running ? 'Scanning…' : '⟳ Scan network'),
        h('button', { class: 'btn', onclick: async () => { try { const r = await api('/api/network/oui/update', { method: 'POST' }); ctx.toast(`Vendor database updated (${r.entries} prefixes)`, 'ok'); } catch (e) { ctx.toast(e.data?.error || e.message, 'err'); } } }, 'Update vendor (OUI) database'),
        h('span', { class: 'muted' }, sc.running ? `${sc.progress} · ${sc.found} found` : meta?.completed_at ? `Last scan ${ago(meta.completed_at)} · methods: ${(meta.methods || []).join(', ') || 'none'}` : 'No scan yet')),
      sc.error ? h('div', { class: 'banner err' }, `Scan failed: ${sc.error}`) : null,
      meta?.skipped_methods && Object.keys(meta.skipped_methods).length ? h('div', { class: 'banner' }, 'Skipped: ' + Object.entries(meta.skipped_methods).map(([k, v]) => `${k} (${v})`).join('; ')) : null,
      devs.length || sc.running ? h('div', { class: 'radar-wrap' }, h('div', { class: 'radar' }, radar(), tip), legend()) : h('div', { class: 'empty' }, 'No devices to show. Run a scan — nothing is displayed until real devices are discovered.'),
      devs.length ? table() : null,
      known.filter((k) => k.status === 'offline').length ? h('div', { class: 'card', style: 'margin-top:12px' }, h('h3', {}, 'Previously seen, not in the latest scan'), ...known.filter((k) => k.status === 'offline').map((k) => h('div', { class: 'muted' }, `${k.hostname || k.ip} · ${k.mac} · last seen ${fmtDateTime(k.last_seen)}`))) : null);
  }
  function legend() { const types = [...new Set(devs.map((d) => d.device_type))]; return h('div', {}, h('div', { class: 'card' }, h('h3', {}, 'Summary'), h('div', {}, `${devs.length} device(s) online`), h('div', { class: 'muted', style: 'font-size:12px' }, (meta?.networks || []).map((n) => `${n.iface} ${n.network}`).join(' · ')), h('div', { class: 'row', style: 'margin-top:8px' }, ...types.map((t) => h('span', { class: 'pill', style: `border-color:${colorOf(t)};color:${colorOf(t)}` }, `${glyph(t)} ${t} (${devs.filter((d) => d.device_type === t).length})`)))), h('div', { class: 'attrib' }, 'Connection type (wired/Wi-Fi) cannot be observed from this host and is reported as Unknown. Manufacturer comes from the MAC prefix; randomised (private) MACs show no manufacturer.')); }
  function table() {
    return h('div', { class: 'card', style: 'margin-top:12px; overflow:auto' }, h('table', {}, h('thead', {}, h('tr', {}, ['Device name', 'IP', 'MAC', 'Manufacturer', 'Type', 'Connection', 'Status', 'Last seen', 'Discovery'].map((c) => h('th', {}, c)))),
      h('tbody', {}, ...devs.map((d) => h('tr', {}, h('td', {}, d.name || '—', d.is_self ? h('span', { class: 'pill info', style: 'margin-left:6px' }, 'this PC') : null), h('td', { class: 'mono' }, d.ip), h('td', { class: 'mono' }, d.mac || '—'),
        h('td', {}, d.vendor || (d.randomized_mac ? 'Private (randomised) MAC' : 'Unknown')), h('td', {}, d.device_type), h('td', {}, d.connection), h('td', {}, h('span', { class: 'pill ok' }, d.status)), h('td', {}, ago(d.last_seen)), h('td', {}, d.methods.join(', ')))))));
  }
  return { show: load, update() { if (ctx.active() === 'network') render(); }, event(ev) { if (ev.type === 'network.scan_completed') load(); } };
}
