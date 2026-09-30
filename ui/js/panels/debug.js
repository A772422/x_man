import { h, clear, fmtTime, put } from '../dom.js';
import { api } from '../net.js';

export function create(ctx) {
  const root = ctx.root; let tab = 'events'; let data = null;
  async function load() {
    try {
      if (tab === 'events') data = (await api('/api/events/recent?limit=200')).events;
      else if (tab === 'audit') data = (await api('/api/audit?limit=150')).calls;
      else data = ((await api('/api/tools')).tools);
    } catch (e) { data = { error: e.message }; }
    render();
  }
  function render() {
    clear(root);
    put(root, h('h2', {}, 'Developer console'), h('p', { class: 'sub' }, 'Structured logs. Arguments are redacted; secrets never appear here.'),
      h('div', { class: 'tabs' }, ...['events', 'audit', 'tools'].map((t) => h('button', { class: t === tab ? 'active' : '', onclick: () => { tab = t; load(); } }, t === 'audit' ? 'tool audit log' : t))),
      h('div', { class: 'row', style: 'margin-bottom:8px' }, h('button', { class: 'btn small', onclick: load }, 'Refresh')));
    if (!data) return;
    if (data.error) { put(root, h('div', { class: 'banner err' }, data.error)); return; }
    if (tab === 'events') put(root, h('div', { class: 'card mono', style: 'font-size:11.5px' }, ...data.slice().reverse().map((e) => h('div', { class: 'line' }, h('span', { class: 't' }, fmtTime(e.ts)), h('span', { class: 'x' }, e.type), h('span', { class: 'd' }, JSON.stringify(e.data).slice(0, 220))))));
    else if (tab === 'audit') put(root, h('div', { class: 'card' }, h('table', {}, h('thead', {}, h('tr', {}, ['time', 'task', 'tool', 'status', 'ms', 'verified', 'arguments (redacted)', 'error'].map((c) => h('th', {}, c)))),
      h('tbody', {}, ...data.map((c) => h('tr', {}, h('td', {}, fmtTime(c.ts)), h('td', { class: 'mono' }, c.task_id || ''), h('td', {}, c.tool), h('td', {}, h('span', { class: 'pill ' + (c.status === 'completed' ? 'ok' : 'err') }, c.status)), h('td', {}, c.duration_ms), h('td', {}, c.verified == null ? '—' : c.verified ? '✓' : '✗'), h('td', { class: 'mono' }, (c.arguments_redacted || '').slice(0, 120)), h('td', {}, c.error || '')))))));
    else put(root, h('div', { class: 'card' }, h('table', {}, h('thead', {}, h('tr', {}, ['tool', 'plugin', 'confirmation level', 'available'].map((c) => h('th', {}, c)))),
      h('tbody', {}, ...data.map((t) => h('tr', {}, h('td', { title: t.description }, t.name), h('td', {}, t.plugin), h('td', {}, ['', 'automatic', 'confirm', 'always confirm'][t.level]), h('td', {}, t.available ? '✓' : h('span', { class: 'muted' }, '✗ ' + t.unavailable_reason))))))));
  }
  return { show: load, update() {} };
}
