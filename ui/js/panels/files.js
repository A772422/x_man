import { h, clear, fmtBytes, fmtDateTime, put } from '../dom.js';
import { api, tool } from '../net.js';

export function create(ctx) {
  const root = ctx.root; let path = '~'; let listing = null; let sel = null; let preview = null; let err = '';
  async function ls(p) {
    try { listing = await api('/api/files/list?path=' + encodeURIComponent(p)); path = listing.path; err = ''; sel = null; preview = null; } catch (e) { err = e.message; }
    render();
  }
  async function act(name, args, ok) { const r = await tool(name, args); if (!r.success) ctx.toast(`${name}: ${r.error}`, r.status === 'declined' ? 'warn' : 'err'); else ctx.toast(ok || `${name} ✓ ${r.verification || ''}`, 'ok'); await ls(path); }
  async function select(e) {
    sel = e; preview = null; render();
    if (!e.is_dir) { try { preview = (await api('/api/files/read?path=' + encodeURIComponent(e.path))); } catch (x) { preview = { error: x.message }; } render(); }
  }
  function render() {
    clear(root);
    const pin = h('input', { type: 'text', class: 'grow mono', value: path }); pin.addEventListener('keydown', (e) => e.key === 'Enter' && ls(pin.value));
    const ask = (label, def = '') => window.prompt(label, def);
    put(root, h('h2', {}, 'File manager'), h('p', { class: 'sub' }, 'Operates on your real files. Deletes go to the M.R.X. trash and ask for confirmation.'),
      h('div', { class: 'row', style: 'margin-bottom:10px' }, h('button', { class: 'btn', onclick: () => listing && ls(listing.parent) }, '↑ Up'), pin, h('button', { class: 'btn', onclick: () => ls(pin.value) }, 'Go'),
        h('button', { class: 'btn', onclick: () => { const n = ask('New folder name'); n && act('create_folder', { path: `${path}/${n}` }); } }, '+ Folder'),
        h('button', { class: 'btn', onclick: () => { const n = ask('New file name'); n && act('create_file', { path: `${path}/${n}`, content: '' }); } }, '+ File')),
      err ? h('div', { class: 'banner err' }, err) : null,
      h('div', { class: 'files' },
        h('div', { class: 'card list' }, listing ? (listing.entries.length ? listing.entries.map((e) => h('div', { class: 'frow' + (sel?.path === e.path ? ' sel' : ''), onclick: () => select(e), ondblclick: () => e.is_dir && ls(e.path) },
          h('span', {}, e.is_dir ? '📁' : '📄'), h('span', { class: 'n' }, e.name), h('span', { class: 's' }, e.is_dir ? '' : fmtBytes(e.size)))) : h('div', { class: 'empty' }, 'Empty folder')) : h('div', { class: 'empty' }, 'Loading…')),
        h('div', { class: 'card preview' }, sel ? [h('h3', {}, sel.name), h('div', { class: 'muted', style: 'font-size:12px;margin-bottom:8px' }, `${sel.is_dir ? 'Folder' : fmtBytes(sel.size)} · modified ${fmtDateTime(sel.modified)}`),
          h('div', { class: 'row', style: 'margin-bottom:8px' },
            sel.is_dir ? h('button', { class: 'btn small', onclick: () => ls(sel.path) }, 'Open folder') : null,
            h('button', { class: 'btn small', onclick: () => act('open_path', { path: sel.path }, 'Open request sent') }, 'Open with default app'),
            h('button', { class: 'btn small', onclick: () => act('open_path', { path: sel.path, reveal: true }, 'Reveal request sent') }, 'Reveal'),
            h('button', { class: 'btn small', onclick: () => { const n = ask('Rename to', sel.name); n && n !== sel.name && act('rename_file', { path: sel.path, new_name: n }); } }, 'Rename'),
            h('button', { class: 'btn small', onclick: () => { const d = ask('Copy to (path)', path); d && act('copy_file', { src: sel.path, dst: d }); } }, 'Copy'),
            h('button', { class: 'btn small', onclick: () => { const d = ask('Move to (path)', path); d && act('move_file', { src: sel.path, dst: d }); } }, 'Move'),
            h('button', { class: 'btn small danger', onclick: () => act(sel.is_dir ? 'delete_folder' : 'delete_file', { path: sel.path }) }, 'Delete')),
          preview ? (preview.error ? h('div', { class: 'banner' }, preview.error) : h('pre', { class: 'pre' }, preview.content + (preview.truncated ? '\n… (truncated)' : ''))) : (sel.is_dir ? null : h('div', { class: 'muted' }, 'Loading preview…'))] : h('div', { class: 'empty' }, 'Select a file or folder'))));
  }
  return { show() { listing ? render() : ls(path); }, update() {} };
}
