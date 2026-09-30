import { h, clear, fmtDateTime, put } from '../dom.js';
import { stepIcon } from '../state.js';
import { api } from '../net.js';

const pill = (s, o) => h('span', { class: 'pill ' + ({ COMPLETED: o === 'PARTIALLY_COMPLETED' ? 'warn' : 'ok', FAILED: 'err', CANCELLED: 'warn', RUNNING: 'info', WAITING: 'warn' }[s] || '') }, s === 'COMPLETED' && o === 'PARTIALLY_COMPLETED' ? 'PARTIALLY COMPLETED' : s);

export function renderTask(t, ctx, compact = false) {
  const live = !['COMPLETED', 'FAILED', 'CANCELLED'].includes(t.status);
  const act = (a, label, cls = '') => h('button', { class: 'btn small ' + cls, onclick: () => api(`/api/tasks/${t.id}/${a}`, { method: 'POST' }).catch((e) => ctx.toast(e.message, 'err')) }, label);
  return h('div', { class: 'task' },
    h('div', { class: 'head' }, h('div', { class: 'cmd' }, t.command), pill(t.status, t.outcome),
      live ? [t.status === 'WAITING' ? act('resume', '▶ Resume') : act('pause', '❚❚ Pause'), act('cancel', '■ Cancel', 'danger')] : act('retry', '↻ Retry')),
    h('div', { class: 'progress' }, h('i', { style: `width:${Math.round((t.progress || 0) * 100)}%` })),
    h('div', { class: 'muted', style: 'font-size:12px' }, `${t.id} · created ${fmtDateTime(t.created_at)}${t.current_action && live ? ' · ' + t.current_action : ''}`),
    compact ? null : h('div', { class: 'steps' }, ...(t.steps || []).map((s) => h('div', { class: 'step ' + s.status }, h('span', { class: 'ic' }, stepIcon(s.status)), h('span', {}, s.title), s.detail ? h('span', { class: 'd' }, '— ' + s.detail) : null))),
    t.error ? h('div', { class: 'banner err', style: 'margin:8px 0 0' }, t.error) : null,
    !live && t.status === 'FAILED' ? h('div', { class: 'row', style: 'margin-top:8px' }, h('span', { class: 'muted' }, 'Available actions:'), act('retry', '↻ Retry')) : null);
}

export function create(ctx) {
  const root = ctx.root;
  return {
    show() { this.update(); },
    update() {
      clear(root);
      put(root, h('h2', {}, 'Task manager'), h('p', { class: 'sub' }, 'Every command becomes a task with live steps, progress, and pause / resume / cancel / retry.'));
      const ids = ctx.state.order;
      if (!ids.length) { put(root, h('div', { class: 'empty' }, 'No tasks yet. Give M.R.X. a command from the dashboard.')); return; }
      ids.forEach((id) => put(root, renderTask(ctx.state.tasks[id], ctx)));
    },
  };
}
