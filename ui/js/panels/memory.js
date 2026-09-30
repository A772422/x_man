import { h, clear, fmtDateTime, put } from '../dom.js';
import { api } from '../net.js';

export function create(ctx) {
  const root = ctx.root; let items = []; let cats = []; let cat = ''; let q = ''; let err = '';
  async function load() { try { const d = await api(`/api/memory?q=${encodeURIComponent(q)}${cat ? '&category=' + cat : ''}`); items = d.memories; cats = d.categories; err = ''; } catch (e) { err = e.message; } render(); }
  async function add(content, category) { try { await api('/api/memory', { method: 'POST', body: { content, category } }); ctx.toast('Remembered', 'ok'); load(); } catch (e) { ctx.toast(e.message, 'err'); } }
  function render() {
    clear(root);
    const nc = h('textarea', { rows: 2, placeholder: 'Something to remember, e.g. “My project folder is on the D drive”. Passwords and API keys are refused.', class: 'grow' }); const nk = h('select', {}, ...(cats.length ? cats : ['long_term']).map((c) => h('option', { value: c }, c.replace('_', ' '))));
    const s = h('input', { type: 'search', class: 'grow', placeholder: 'Search memory…', value: q }); s.addEventListener('keydown', (e) => { if (e.key === 'Enter') { q = s.value; load(); } });
    put(root, h('h2', {}, 'Memory'), h('p', { class: 'sub' }, 'Everything M.R.X. remembers is stored locally in SQLite. Retrieval uses a local n-gram similarity index (no cloud). You can inspect, edit and delete any entry.'),
      err ? h('div', { class: 'banner err' }, err) : null,
      h('div', { class: 'card' }, h('div', { class: 'row' }, nc, nk, h('button', { class: 'btn primary', onclick: () => nc.value.trim() && add(nc.value.trim(), nk.value) }, 'Remember'))),
      h('div', { class: 'row', style: 'margin:12px 0' }, s, h('button', { class: 'btn', onclick: () => { q = s.value; load(); } }, 'Search'), h('select', { onchange: (e) => { cat = e.target.value; load(); } }, h('option', { value: '' }, 'all categories'), ...cats.map((c) => h('option', { value: c, selected: c === cat }, c.replace('_', ' ')))),
        h('button', { class: 'btn danger', onclick: async () => { if (confirm('Delete ALL memories? This cannot be undone.')) { await api('/api/memory' + (cat ? '?category=' + cat : ''), { method: 'DELETE' }); load(); } } }, cat ? `Clear “${cat}”` : 'Clear all')),
      items.length ? h('div', { class: 'card' }, h('table', {}, h('thead', {}, h('tr', {}, ['id', 'category', 'memory', 'updated', ''].map((c) => h('th', {}, c)))),
        h('tbody', {}, ...items.map((m) => h('tr', {}, h('td', { class: 'mono' }, m.id), h('td', {}, h('span', { class: 'pill' }, m.category.replace('_', ' '))), h('td', {}, m.key ? h('b', {}, m.key + ': ') : null, m.content, m.score != null ? h('span', { class: 'muted' }, `  (match ${m.score})`) : null), h('td', { class: 'muted' }, fmtDateTime(m.updated_at)),
          h('td', {}, h('button', { class: 'btn small', onclick: async () => { const n = prompt('Edit memory', m.content); if (n != null && n.trim()) { try { await api('/api/memory/' + m.id, { method: 'PATCH', body: { content: n } }); load(); } catch (e) { ctx.toast(e.message, 'err'); } } } }, 'Edit'), ' ', h('button', { class: 'btn small danger', onclick: async () => { await api('/api/memory/' + m.id, { method: 'DELETE' }); load(); } }, 'Delete'))))))) : h('div', { class: 'empty' }, q ? 'No matching memories.' : 'Nothing stored yet. Say “remember that …” to add something.'));
  }
  return { show: load, update() {} };
}
