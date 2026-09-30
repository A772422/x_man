// Pure UI state reducer: (state, backend event) -> state. No DOM here, so it is unit-tested with node.
export const MAX_ACTIVITY = 300;

export function initialState() {
  return {
    v: 0, connected: false, status: null, tasks: {}, order: [], messages: [], streaming: {}, activity: [],
    stats: null, hist: { cpu: [], ram: [], up: [], down: [] }, confirmations: [], notices: [],
    running: 0, thinking: 0, speaking: false, listening: false, flash: null, navigate: null,
    scan: { running: false, found: 0, progress: '', last: null, error: null }, youtube: {}, browser: { url: null, ts: 0 },
    activeTask: null,
  };
}

const push = (arr, item, max) => { arr.push(item); if (arr.length > max) arr.splice(0, arr.length - max); };
const ICON = { ok: '✓', err: '✗', run: '…', warn: '!', info: '·' };

export function log(s, ts, kind, text, detail = '') { push(s.activity, { ts, kind, text, detail }, MAX_ACTIVITY); }

export function upsertTask(s, t) {
  if (!s.tasks[t.id]) s.order.unshift(t.id);
  const prev = s.tasks[t.id];
  // Step events arrive individually; a task snapshot with fewer steps must never erase newer ones.
  const steps = t.steps && (!prev || t.steps.length >= prev.steps.length) ? t.steps.map((x) => ({ ...x })) : (prev?.steps || []);
  s.tasks[t.id] = { ...(prev || {}), ...t, steps };
}

export function reduce(s, ev, now = Date.now()) {
  const d = ev.data || {};
  const ts = ev.ts || now / 1000;
  s.v++;
  switch (ev.type) {
    case 'hello':
      s.status = d.status; (d.tasks || []).slice().reverse().forEach((t) => upsertTask(s, t));
      s.confirmations = d.pending_confirmations || []; break;
    case 'agent.input': case 'voice.command':
      push(s.messages, { role: 'user', text: d.text, lang: d.language?.name, ts, voice: ev.type === 'voice.command' }, 400); break;
    case 'agent.started':
      upsertTask(s, d); s.activeTask = d.id; log(s, ts, 'run', `Task started: ${d.command}`); break;
    case 'agent.thinking': s.thinking++; break;
    case 'task.updated': {
      upsertTask(s, d);
      if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(d.status)) {
        s.thinking = Math.max(0, s.thinking - 1);
        const state = d.status === 'CANCELLED' ? 'warning' : d.outcome === 'PARTIALLY_COMPLETED' ? 'warning' : d.status === 'FAILED' ? 'error' : 'success';
        s.flash = { state, until: now + 2600 };
        const kind = state === 'success' ? 'ok' : state === 'error' ? 'err' : 'warn';
        log(s, ts, kind, `Task ${d.status.toLowerCase()}${d.outcome && d.outcome !== d.status ? ' — ' + d.outcome.replace(/_/g, ' ').toLowerCase() : ''}`, d.error || '');
      }
      break;
    }
    case 'task.step': {
      const t = s.tasks[d.task_id]; if (!t) break;
      const i = t.steps.findIndex((x) => x.idx === d.step.idx);
      if (i >= 0) t.steps[i] = { ...d.step }; else t.steps.push({ ...d.step });
      break;
    }
    case 'agent.text_delta': s.streaming[d.task_id] = (s.streaming[d.task_id] || '') + d.text; break;
    case 'agent.message': {
      if (d.task_id) delete s.streaming[d.task_id];
      const t = d.task_id && s.tasks[d.task_id];
      push(s.messages, { role: 'agent', text: d.text, ts, task: d.task_id, outcome: t?.outcome, error: t?.error }, 400); break;
    }
    case 'tool.started': s.running++; log(s, ts, 'run', `${d.tool}`, briefArgs(d.arguments)); break;
    case 'tool.completed': case 'tool.failed': {
      s.running = Math.max(0, s.running - 1);
      const ok = ev.type === 'tool.completed';
      const unobserved = d.verified === null || d.verified === undefined;
      const kind = !ok ? 'err' : unobserved && !d.read_only ? 'warn' : 'ok';
      const note = !ok ? (d.error || 'failed') : (d.verification || (d.verified ? 'verified' : d.read_only ? 'done' : 'sent — effect not independently verifiable'));
      log(s, ts, kind, `${d.tool}`, `${note} · ${d.ms}ms`);
      break;
    }
    case 'confirm.required': if (!s.confirmations.find((c) => c.id === d.id)) s.confirmations.push(d); break;
    case 'confirm.resolved': s.confirmations = s.confirmations.filter((c) => c.id !== d.id); break;
    case 'system.stats': {
      s.stats = d; const h = s.hist;
      push(h.cpu, d.cpu_percent, 60); push(h.ram, d.ram.percent, 60); push(h.up, d.net.up_bps, 60); push(h.down, d.net.down_bps, 60); break;
    }
    case 'system.alert': push(s.notices, { ts, level: 'warn', text: d.message }, 30); log(s, ts, 'warn', `ALERT: ${d.message}`); break;
    case 'network.scan_started': s.scan = { running: true, found: 0, progress: 'Starting scan…', last: s.scan.last, error: null }; log(s, ts, 'run', 'Scanning the local network…'); break;
    case 'network.device_found': s.scan.found++; log(s, ts, 'info', `Device found: ${d.ip}`, d.method); break;
    case 'network.progress': s.scan.progress = d.message; log(s, ts, 'run', d.message); break;
    case 'network.scan_completed': s.scan = { running: false, found: d.count, progress: 'Scan complete', last: ts, error: null }; log(s, ts, 'ok', `Scan complete — ${d.count} device(s)`, (d.methods || []).join(', ')); break;
    case 'network.scan_failed': s.scan.running = false; s.scan.error = d.error; log(s, ts, 'err', 'Network scan failed', d.error); break;
    case 'browser.navigation': s.browser = { url: d.url, ts }; log(s, ts, 'info', `Browser → ${d.url}`); break;
    case 'browser.download': log(s, ts, d.error ? 'err' : 'ok', d.error ? 'Download failed' : `Downloaded ${d.path}`, d.error || ''); break;
    case 'browser.dialog': log(s, ts, 'warn', `Page dialog (${d.type}): ${d.message}`); break;
    case 'youtube.state': s.youtube = { ...s.youtube, ...d }; break;
    case 'ui.navigate': s.navigate = { ...d, n: s.v }; break;
    case 'email.draft_created': log(s, ts, 'ok', `Email draft #${d.draft_id} created (not sent)`, d.subject); break;
    case 'email.sent': log(s, ts, 'ok', `Email sent to ${d.to}`); break;
    case 'agent.control': log(s, ts, 'warn', `Control: ${d.control}`); break;
    default: break;
  }
  return s;
}

function briefArgs(a) { if (!a) return ''; return Object.entries(a).slice(0, 3).map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 40) : JSON.stringify(v).slice(0, 40)}`).join(' '); }

export function avatarState(s, now = Date.now()) {
  if (s.speaking) return 'speaking';
  if (s.confirmations.length) return 'warning';
  if (s.running > 0) return 'executing';
  if (s.thinking > 0) return 'thinking';
  if (s.listening) return 'listening';
  if (s.flash && s.flash.until > now) return s.flash.state;
  return 'idle';
}

export const ICONS = ICON;
export function stepIcon(status) { return { done: '✓', running: '…', failed: '✗', declined: '⊘', sent: '↗', warn: '!' }[status] || '·'; }
