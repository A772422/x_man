import { h, clear, put } from '../dom.js';
import { api, tool, blobUrl } from '../net.js';

export function create(ctx) {
  const root = ctx.root; let st = { running: false, tabs: [] }; let img = null; let busy = false; let last = 0;
  async function refresh(shot = true) {
    try { st = await api('/api/browser/state'); } catch { st = { running: false, tabs: [] }; }
    if (shot && st.running) { try { const u = await blobUrl('/api/browser/screenshot'); if (img) URL.revokeObjectURL(img); img = u; } catch { /* keep previous */ } }
    render();
  }
  async function run(name, args) { busy = true; render(); const r = await tool(name, args); busy = false; if (!r.success) ctx.toast(`${name}: ${r.error}`, 'err'); await refresh(); }
  function render() {
    clear(root);
    const url = h('input', { type: 'text', class: 'grow', placeholder: 'Enter a URL or press Enter to search…', value: st.tabs.find((t) => t.active)?.url || '' });
    const go = () => { const v = url.value.trim(); if (!v) return; /^(https?:\/\/|[\w-]+\.[a-z]{2,})/i.test(v) ? run('navigate', { url: v }) : run('search_web', { query: v }); };
    url.addEventListener('keydown', (e) => e.key === 'Enter' && go());
    put(root, h('h2', {}, 'Browser agent'), h('p', { class: 'sub' }, 'A real Playwright-controlled browser. What you see below is a live screenshot of the actual page M.R.X. is operating.'),
      h('div', { class: 'row', style: 'margin-bottom:10px' }, st.running ? h('button', { class: 'btn danger', onclick: () => run('close_browser', {}) }, 'Close browser') : h('button', { class: 'btn primary', onclick: () => run('open_browser', {}) }, 'Launch browser'),
        url, h('button', { class: 'btn', disabled: !st.running || busy, onclick: go }, 'Go'), h('button', { class: 'btn', disabled: !st.running, onclick: () => refresh() }, '⟳ Refresh view')),
      st.running ? h('div', { class: 'tabs' }, ...st.tabs.map((t) => h('button', { class: t.active ? 'active' : '', title: t.url, onclick: () => run('switch_tab', { index: t.index }) }, (t.title || t.url || 'blank').slice(0, 32))),
        h('button', { onclick: () => run('new_tab', {}) }, '+ tab')) : h('div', { class: 'empty' }, 'The browser is not running.'),
      st.running && img ? h('img', { class: 'browser-shot', src: img, alt: 'Live view of the automation browser' }) : null,
      st.downloads?.length ? h('div', { class: 'card', style: 'margin-top:10px' }, h('h3', {}, 'Recent downloads'), ...st.downloads.map((d) => h('div', { class: 'mono muted' }, `${d.path} (${d.size} bytes)`))) : null);
  }
  return { show() { refresh(); }, update() {}, event(ev) { if (ev.type === 'browser.navigation' && ctx.active() === 'browser' && Date.now() - last > 1200) { last = Date.now(); setTimeout(() => refresh(), 900); } } };
}
