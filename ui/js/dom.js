// Tiny DOM helper. Everything that reaches the page goes through textContent / setAttribute —
// never innerHTML — because news, emails, web pages and file names are untrusted.
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
export const $ = (sel, root = document) => root.querySelector(sel);
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
export function safeUrl(u) { try { const x = new URL(u); return ['http:', 'https:'].includes(x.protocol) ? x.href : null; } catch { return null; } }
export function fmtBytes(n) { if (n == null) return ''; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`; }
export function fmtRate(bps) { return fmtBytes(bps || 0) + '/s'; }
export function fmtTime(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour12: false }) : ''; }
export function fmtDateTime(ts) { return ts ? new Date(ts * 1000).toLocaleString() : 'never'; }
export function ago(ts, now = Date.now() / 1000) { if (!ts) return 'never'; const s = Math.max(0, now - ts); if (s < 60) return `${Math.floor(s)}s ago`; if (s < 3600) return `${Math.floor(s / 60)}m ago`; if (s < 86400) return `${Math.floor(s / 3600)}h ago`; return `${Math.floor(s / 86400)}d ago`; }
export function fmtUptime(s) { const d = Math.floor(s / 86400), hr = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + 'd ' : '') + hr + 'h ' + m + 'm'; }

// append that ignores null/false (DOM append() would print the text "null")
export function put(el, ...kids) {
  for (const c of kids.flat(Infinity)) { if (c == null || c === false) continue; el.append(c instanceof Node ? c : document.createTextNode(String(c))); }
  return el;
}
