import { h, clear, fmtRate, fmtUptime, put } from '../dom.js';

function spark(values, max = 100, color = 'var(--cyan)') {
  const W = 260, H = 34, n = Math.max(values.length, 2);
  const m = max || Math.max(1, ...values);
  const pts = values.map((v, i) => `${(i / (n - 1)) * W},${H - Math.min(1, v / m) * (H - 2) - 1}`).join(' ');
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'spark'); svg.setAttribute('preserveAspectRatio', 'none');
  const p = document.createElementNS(ns, 'polyline'); p.setAttribute('points', pts); p.setAttribute('fill', 'none'); p.setAttribute('stroke', color); p.setAttribute('stroke-width', '1.5');
  svg.append(p); return svg;
}
const level = (v) => (v > 90 ? 'crit' : v > 75 ? 'hot' : '');
function meter(label, value, unit, pct, sub, hist, max) {
  return h('div', { class: 'meter' },
    h('div', { class: 'top' }, h('span', {}, label), h('b', {}, value + (unit || ''))),
    pct != null ? h('div', { class: 'bar ' + level(pct) }, h('i', { style: `width:${Math.min(100, pct)}%` })) : null,
    hist ? spark(hist, max) : null,
    sub ? h('div', { class: 'sub2' }, sub) : null);
}

export function create(ctx) {
  const root = ctx.root;
  return {
    update() {
      const s = ctx.state.stats; clear(root);
      put(root, h('div', { class: 'panel-title mono muted', style: 'font-size:11px;letter-spacing:.2em;margin-bottom:10px' }, 'SYSTEM MONITOR'));
      if (!s) { put(root, h('div', { class: 'empty' }, ctx.state.connected ? 'Waiting for telemetry…' : 'Not connected')); return; }
      const hist = ctx.state.hist;
      put(root, 
        meter('CPU', s.cpu_percent.toFixed(0), '%', s.cpu_percent, `${s.cpu_cores} logical cores`, hist.cpu, 100),
        meter('RAM', s.ram.percent.toFixed(0), '%', s.ram.percent, `${s.ram.used_gb} / ${s.ram.total_gb} GB`, hist.ram, 100),
        s.gpu ? meter('GPU', s.gpu.util_percent.toFixed(0), '%', s.gpu.util_percent, `${s.gpu.name} · VRAM ${(s.gpu.mem_used_mb / 1024).toFixed(1)}/${(s.gpu.mem_total_mb / 1024).toFixed(1)} GB · ${s.gpu.temp_c}°C`)
          : meter('GPU', 'n/a', '', null, 'No NVIDIA GPU / nvidia-smi detected — not reported'),
        s.disk ? meter('DISK', s.disk.percent.toFixed(0), '%', s.disk.percent, `${s.disk.used_gb} / ${s.disk.total_gb} GB`) : null,
        meter('NET ↑', fmtRate(s.net.up_bps), '', null, null, hist.up, 0),
        meter('NET ↓', fmtRate(s.net.down_bps), '', null, null, hist.down, 0),
        s.temperatures_c ? meter('TEMP', Math.max(...Object.values(s.temperatures_c)).toFixed(0), '°C', null, Object.entries(s.temperatures_c).slice(0, 3).map(([k, v]) => `${k} ${v}°`).join(' · ')) : meter('TEMP', 'n/a', '', null, 'Sensors not exposed by this OS/hardware'),
        s.battery ? meter('BATTERY', s.battery.percent.toFixed(0), '%', 100 - s.battery.percent, s.battery.plugged ? 'plugged in' : 'on battery') : null,
        meter('UPTIME', fmtUptime(s.uptime_s), '', null, `${s.process_count} processes`),
        h('div', { class: 'meter' }, h('div', { class: 'top' }, h('span', {}, 'Top by memory')),
          ...s.top_processes.map((p) => h('div', { class: 'proc' }, h('span', {}, p.name), h('span', { class: 'mono' }, `${p.memory_mb} MB`)))));
    },
  };
}
