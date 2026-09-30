import { h, $, clear, fmtTime, put } from './dom.js';
import { api, Events } from './net.js';
import { initialState, reduce, avatarState, stepIcon } from './state.js';
import { Voice } from './voice.js';
import * as monitor from './panels/monitor.js';
import * as tasks from './panels/tasks.js';
import * as browser from './panels/browser.js';
import * as youtube from './panels/youtube.js';
import * as files from './panels/files.js';
import * as news from './panels/news.js';
import * as map from './panels/map.js';
import * as network from './panels/network.js';
import * as memory from './panels/memory.js';
import * as settings from './panels/settings.js';
import * as debug from './panels/debug.js';

const state = initialState();
let cfg = { voice: { mode: 'push_to_talk', language: 'auto', wake_word: 'hey mrx', speech_rate: 1, speak_replies: true } };
let active = 'dashboard'; let interim = ''; let level = 0; let vad = false; let raf = 0;
const CONVO = 'main';

const NAV = [['dashboard', '◎', 'Dashboard'], ['tasks', '☰', 'Tasks'], ['browser', '◐', 'Browser'], ['youtube', '▶', 'YouTube'], ['files', '▤', 'Files'], ['news', '✉', 'News'],
  ['map', '◍', 'World Map'], ['network', '⌖', 'Network'], ['memory', '❖', 'Memory'], ['settings', '⚙', 'Settings'], ['debug', '⌘', 'Debug']];

// ---------------------------------------------------------------------------------------------- toasts / modal
function toast(text, kind = '', ms = 4500) {
  const t = h('div', { class: 'toast ' + kind, role: 'status' }, text); $('#toasts').append(t); setTimeout(() => t.remove(), ms);
}
function renderModal() {
  const root = $('#modal-root'); const c = state.confirmations[0];
  if (!c) { if (root.firstChild?.dataset.cid) clear(root); return; }   // only ever remove a confirmation dialog, never the API-key dialog
  if (root.firstChild?.dataset.cid === c.id) return;
  const answer = (approved) => { events.send({ type: 'confirm', id: c.id, approved }); state.confirmations = state.confirmations.filter((x) => x.id !== c.id); renderModal(); schedule(); };
  const cancel = h('button', { class: 'btn', onclick: () => answer(false) }, 'Cancel');
  const back = h('div', { class: 'modal-back', dataset: { cid: c.id }, onkeydown: (e) => e.key === 'Escape' && answer(false) },
    h('div', { class: 'modal lvl' + c.level, role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': 'cm-t' },
      h('h3', { id: 'cm-t' }, c.level >= 3 ? '⚠ Confirm — irreversible or high impact' : 'Confirm action'),
      h('div', {}, h('b', {}, c.tool.replace(/_/g, ' ')), ' — ', c.summary), h('pre', { class: 'pre args' }, JSON.stringify(c.arguments, null, 2)),
      state.confirmations.length > 1 ? h('div', { class: 'muted' }, `${state.confirmations.length - 1} more waiting`) : null,
      h('div', { class: 'row' }, cancel, h('button', { class: 'btn ' + (c.level >= 3 ? 'danger' : 'primary'), onclick: () => answer(true) }, 'Confirm'))));
  put(clear(root), back); cancel.focus();
}

// ---------------------------------------------------------------------------------------------- AI key / test
async function testAi() {
  toast('Testing the AI connection…', '', 2500);
  try { const r = await api('/api/ai/test', { method: 'POST' });
    (r.results?.length ? r.results : [{ ok: r.ok, model: 'AI', error: r.error, ms: r.ms }]).forEach((x) => x.ok ? toast(`${x.model} works (${x.ms} ms)`, 'ok', 6000) : toast(`${x.model || x.provider}: ${x.error}`, 'err', 14000));
  } catch (e) { toast(`Test failed: ${e.message}`, 'err', 8000); }
  refreshStatus();
}
function askKey(name = 'AI') {
  const isAi = name === 'AI' || name === 'ANTHROPIC_API_KEY' || name === 'GEMINI_API_KEY';
  const pick = h('select', {}, h('option', { value: 'GEMINI_API_KEY' }, 'Google Gemini — free key from aistudio.google.com/apikey'), h('option', { value: 'ANTHROPIC_API_KEY', selected: name === 'ANTHROPIC_API_KEY' }, 'Anthropic Claude — paid API credits'));
  if (name === 'GEMINI_API_KEY') pick.value = 'GEMINI_API_KEY';
  const root = $('#modal-root'); const inp = h('input', { type: 'password', class: 'grow', placeholder: isAi ? 'paste your key here' : 'value', autocomplete: 'off', spellcheck: 'false' });
  const close = () => clear(root); const msg = h('div', { class: 'muted', style: 'min-height:18px' });
  const save = async (allowFile = false) => {
    const v = inp.value.trim(); if (!v) return; msg.textContent = 'Saving…'; fileBtn.style.display = 'none';
    try { const r = await api('/api/secrets/' + (isAi ? pick.value : name), { method: 'POST', body: { value: v, allow_file: allowFile } }); inp.value = ''; close(); toast(`Key stored in ${r.stored === 'file' ? 'a private file (~/.mrx/.env)' : 'the OS keychain'}`, 'ok', 4000); if (isAi) await api('/api/settings', { method: 'PATCH', body: { ai: { provider: pick.value === 'GEMINI_API_KEY' ? 'gemini' : 'anthropic' } } }); await refreshStatus(); if (isAi) await testAi(); if (active === 'settings') panels.settings.show(); }
    catch (e) { if (e.data?.needs_file_fallback) { msg.textContent = 'The OS keychain did not accept/return the value. You can store it in a private file in your M.R.X. folder (~/.mrx/.env) instead.'; fileBtn.style.display = ''; } else msg.textContent = e.message; }
  };
  const fileBtn = h('button', { class: 'btn', style: 'display:none', onclick: () => save(true) }, 'Save to private file instead');
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(false); if (e.key === 'Escape') close(); });
  put(clear(root), h('div', { class: 'modal-back', onclick: (e) => e.target === e.currentTarget && close() }, h('div', { class: 'modal', role: 'dialog', 'aria-label': 'Enter secret' },
    h('h3', {}, isAi ? 'Enable the AI engine' : `Set ${name}`), isAi ? h('div', { class: 'row' }, pick) : null, h('p', { class: 'muted' }, isAi ? 'Choose the provider and paste its API key. It is stored in the OS keychain (Windows Credential Manager) or a private file, takes effect immediately, and is never shown again or sent anywhere except that provider. The free Gemini tier has request-per-minute/day limits. Tip: Gemini keys should start with AIza — keys starting with AQ. are often rejected by Google; create the key in an incognito window at aistudio.google.com/apikey if the test fails.' : `Stored privately and used only by M.R.X. It is never shown again.`),
    h('div', { class: 'row' }, inp), msg, h('div', { class: 'row' }, h('button', { class: 'btn', onclick: close }, 'Cancel'), fileBtn, h('button', { class: 'btn primary', onclick: () => save(false) }, 'Save & test'))))); inp.focus();
}

// ---------------------------------------------------------------------------------------------- voice
const voice = new Voice({
  getSettings: () => cfg.voice,
  onCommand: (t) => send(t, 'voice'),
  onStop: () => stopAll(),
  onState: (st) => { state.listening = st === 'listening' || st === 'awaiting'; state.speaking = st === 'speaking'; $('#btn-mic').classList.toggle('rec', voice.active); schedule(); if (st === 'awaiting') toast('Listening for your command…', 'ok', 2500); },
  onInterim: (t, fin) => { interim = fin ? '' : t; events.send({ type: 'voice.input', data: { text: t, final: fin } }); schedule(); },
  onError: (m) => toast(m, 'err', 7000),
});
const meter = (lv, isVad) => { level = lv; vad = isVad; };

function toggleMic() {
  const mode = cfg.voice.mode;
  if (voice.active) { voice.stop(); voice.stopMeter(); level = 0; return; }
  if (!voice.supported) { toast('Speech recognition needs Chrome or Edge. You can still type commands.', 'warn', 6000); return; }
  voice.startMeter(null, meter); voice.start(mode);
}

// ---------------------------------------------------------------------------------------------- send / stop
function send(text, source = 'text') {
  text = (text || '').trim(); if (!text) return;
  voice.cancelSpeech();  // a new command interrupts anything being spoken
  events.send({ type: 'chat', text, conversation_id: CONVO, source });
}
function stopAll() { voice.cancelSpeech(); events.send({ type: 'chat', text: 'stop', conversation_id: CONVO, source: 'ui' }); }

// ---------------------------------------------------------------------------------------------- events
const panels = {}; const listeners = [];
const events = new Events((ev) => {
  reduce(state, ev);
  if (ev.type === 'hello') { cfg.voice = { ...cfg.voice, ...(ev.data.voice || {}) }; }
  if (ev.type === 'agent.text_delta') voice.delta(ev.data.task_id, ev.data.text);
  if (ev.type === 'agent.message') { voice.final(ev.data.task_id || 'x', ev.data.text); }
  if (ev.type === 'voice.interrupt') voice.cancelSpeech();
  if (ev.type === 'ui.navigate' && ev.data.panel) go(ev.data.panel, ev.data);
  if (['tool.completed', 'tool.failed', 'task.completed'].includes(ev.type)) refreshStatusSoon();
  if (ev.type === 'system.alert') toast(ev.data.message, 'warn', 8000);
  if (ev.type === 'confirm.required') { if (document.hidden) document.title = '⚠ Confirm — M.R.X.'; }
  if (ev.type === 'confirm.resolved') document.title = 'M.R.X. — Command Center';
  if (ev.type === 'task.updated' && ev.data.status === 'FAILED') toast(`Task failed: ${ev.data.error || 'see the Tasks panel'}`, 'err', 7000);
  for (const p of Object.values(panels)) p.event?.(ev);
  schedule();
}, (up) => { state.connected = up; const c = $('#conn'); c.textContent = up ? 'live' : 'reconnecting…'; c.className = 'conn ' + (up ? 'on' : 'off'); schedule(); });

let statusTimer = 0;
function refreshStatusSoon() { clearTimeout(statusTimer); statusTimer = setTimeout(refreshStatus, 800); }
async function refreshStatus() { try { state.status = await api('/api/system/status'); } catch { /* connection banner shows it */ } schedule(); }

// ---------------------------------------------------------------------------------------------- rendering
function schedule() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); }
let lastMsgCount = -1; let lastStream = ''; let lastBusy = -1;
function render() {
  const st = avatarState(state);
  $('#app').dataset.avatar = st; $('#avatar').dataset.state = st; $('#avatar-label').textContent = st.toUpperCase();
  if (state.flash && state.flash.until > Date.now()) setTimeout(schedule, state.flash.until - Date.now() + 30);
  renderChips(); renderConvo(); renderConsole(); renderVoiceBar(); renderModal(); renderNavBadge();
  if (state.status?.version) { const tg = $('.tag'); const t = `Real-time autonomous agent · v${state.status.version}`; if (tg.textContent !== t) tg.textContent = t; }
  if (state.status) {
    const note = $('#engine-note'); const online = state.status.engine === 'llm'; const sig = String(online) + state.status.model;
    if (note.dataset.sig !== sig) { note.dataset.sig = sig; clear(note);
      put(note, online ? `AI engine online · ${state.status.model} ` : 'Offline command engine — simple commands only. ',
        h('button', { class: 'btn small', onclick: online ? () => testAi() : () => askKey('AI') }, online ? 'Test connection' : 'Enable AI engine…')); }
  }
  $('#console-task').textContent = state.activeTask && state.tasks[state.activeTask] ? `${state.tasks[state.activeTask].command} — ${(state.tasks[state.activeTask].outcome && state.tasks[state.activeTask].status === 'COMPLETED' ? state.tasks[state.activeTask].outcome : state.tasks[state.activeTask].status).replace(/_/g, ' ')}` : '';
  if (active === 'tasks') panels.tasks.update(); if (active === 'network') panels.network.update?.();
  panels.monitor.update();
  if (state.navigate) { const n = state.navigate; state.navigate = null; go(n.panel, n); }
}
function renderChips() {
  const box = $('#chips'); const s = state.status?.services; const sig = JSON.stringify(s);
  if (box.dataset.sig === sig) return; box.dataset.sig = sig; clear(box);
  Object.entries(s || {}).forEach(([k, v]) => put(box, h('span', { class: 'chip ' + v.state, title: `${k}: ${v.state} — ${v.detail || ''}` }, h('i'), `${k} · ${v.state}`)));
}
function renderNavBadge() { const b = $('#nav [data-p=tasks] .badge'); const n = Object.values(state.tasks).filter((t) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(t.status)).length; if (b) { b.textContent = n || ''; b.style.display = n ? '' : 'none'; } }
function renderConvo() {
  const box = $('#convo'); const streaming = Object.entries(state.streaming).map(([k, v]) => v).join('\n');
  const busySig = Object.values(state.tasks).filter((t) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(t.status)).length;
  if (lastMsgCount === state.messages.length && lastStream === streaming && lastBusy === busySig) return; lastMsgCount = state.messages.length; lastStream = streaming; lastBusy = busySig;
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40; clear(box);
  if (!state.messages.length && !streaming) put(box, h('div', { class: 'msg sys' }, 'Ready. Try: “Open Chrome”, “Create a folder called Project X on my desktop”, “Scan my network”, or press 🎤 and speak (Hindi, English, Hinglish…).'));
  state.messages.forEach((m) => put(box, h('div', { class: 'msg ' + (m.role === 'user' ? 'user' : 'agent' + (m.outcome === 'FAILED' ? ' err' : m.outcome === 'PARTIALLY_COMPLETED' ? ' partial' : '')) }, m.text,
    h('div', { class: 'meta' }, `${fmtTime(m.ts)}${m.voice ? ' · voice' : ''}${m.lang && m.role === 'user' ? ' · ' + m.lang : ''}${m.outcome ? ' · ' + m.outcome.replace(/_/g, ' ').toLowerCase() : ''}`))));
  if (streaming) put(box, h('div', { class: 'msg agent streaming' }, streaming, h('div', { class: 'meta' }, 'streaming…')));
  if (atBottom || streaming) box.scrollTop = box.scrollHeight;
}
function renderConsole() {
  const body = $('#console-body'); const acts = state.activity.slice(-80);
  const sig = state.v; if (body.dataset.v === String(sig)) return; body.dataset.v = String(sig);
  const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 30; clear(body);
  const t = state.activeTask && state.tasks[state.activeTask];
  if (t) put(body, h('div', { class: 'line' }, h('span', { class: 't' }, 'TASK'), h('span', { class: 'x' }, t.command)), ...(t.steps || []).slice(-8).map((s) => h('div', { class: 'line ' + ({ done: 'ok', failed: 'err', declined: 'err', running: 'run', sent: 'warn', warn: 'warn' }[s.status] || '') }, h('span', { class: 't' }), h('span', { class: 'ic' }, stepIcon(s.status)), h('span', { class: 'x' }, s.title), s.detail ? h('span', { class: 'd' }, '— ' + s.detail) : null)));
  acts.forEach((a) => put(body, h('div', { class: 'line ' + a.kind }, h('span', { class: 't' }, fmtTime(a.ts)), h('span', { class: 'ic' }, { ok: '✓', err: '✗', run: '…', warn: '!', info: '·' }[a.kind] || '·'), h('span', { class: 'x' }, a.text), a.detail ? h('span', { class: 'd' }, a.detail) : null)));
  if (!acts.length && !t) put(body, h('div', { class: 'dim' }, 'Waiting for activity. Every tool call, verification and failure appears here live.'));
  if (atBottom) body.scrollTop = body.scrollHeight;
}
function renderVoiceBar() {
  const bar = $('#voice-bar'); const mode = { push_to_talk: 'push-to-talk', wake_word: `wake word “${cfg.voice.wake_word}”`, continuous: 'continuous' }[cfg.voice.mode];
  const sig = `${voice.active}|${interim}|${voice.supported}|${cfg.voice.mode}|${state.speaking}|${Math.round(level * 10)}|${vad}`; if (bar.dataset.sig === sig) return; bar.dataset.sig = sig; clear(bar);
  put(bar, h('span', {}, `Voice: ${voice.supported ? mode : 'unsupported in this browser'}`), voice.active ? h('span', { class: 'level', title: 'microphone level' }, h('i', { style: `width:${Math.round(level * 100)}%` })) : null, voice.active && vad ? h('span', { class: 'pill ok' }, 'speech detected') : null,
    state.speaking ? h('button', { class: 'btn small', onclick: () => voice.cancelSpeech() }, '🔇 Stop speaking') : null, interim ? h('span', { class: 'interim' }, `“${interim}”`) : null);
}

// ---------------------------------------------------------------------------------------------- nav / boot
function go(name, extra) {
  if (!document.getElementById('panel-' + name)) return;
  active = name; document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === 'panel-' + name));
  document.querySelectorAll('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.p === name));
  panels[name]?.show?.(extra); if (name === 'map' && extra?.center) setTimeout(() => panels.map.focus(extra.center), 250);
  schedule();
}
function boot() {
  const nav = $('#nav'); NAV.forEach(([k, ic, label]) => nav.append(h('button', { dataset: { p: k }, class: k === 'dashboard' ? 'active' : '', onclick: () => go(k) }, h('span', { class: 'ico' }, ic), label, k === 'tasks' ? h('span', { class: 'badge', style: 'display:none' }) : null)));
  const ctx = (name) => ({ state, root: $('#panel-' + name), send: (m) => events.send(m), toast, active: () => active, go, voice, meter,
    askSecret: askKey, settingsChanged: (c) => { cfg = c; schedule(); }, refreshStatus, clearLocal: () => { state.messages = []; state.tasks = {}; state.order = []; state.activity = []; lastMsgCount = -1; schedule(); } });
  Object.assign(panels, { monitor: monitor.create({ ...ctx('dashboard'), root: $('#monitor') }), tasks: tasks.create(ctx('tasks')), browser: browser.create(ctx('browser')), youtube: youtube.create(ctx('youtube')), files: files.create(ctx('files')),
    news: news.create(ctx('news')), map: map.create(ctx('map')), network: network.create(ctx('network')), memory: memory.create(ctx('memory')), settings: settings.create(ctx('settings')), debug: debug.create(ctx('debug')) });
  $('#composer').addEventListener('submit', (e) => { e.preventDefault(); const i = $('#input'); send(i.value); i.value = ''; });
  $('#btn-mic').addEventListener('click', toggleMic); $('#btn-stop').addEventListener('click', stopAll);
  $('#console-toggle').addEventListener('click', (e) => { const c = $('#console'); c.classList.toggle('collapsed'); e.target.textContent = c.classList.contains('collapsed') ? 'expand' : 'collapse'; });
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.code === 'Space') { e.preventDefault(); toggleMic(); }
    else if (e.key === 'Escape' && !state.confirmations.length && document.activeElement?.tagName !== 'INPUT') stopAll();
  });
  document.addEventListener('visibilitychange', () => document.body.classList.toggle('paused', document.hidden));   // no animation work in a hidden tab
  api('/api/settings').then((d) => { cfg = d.settings; schedule(); }).catch(() => {});
  api('/api/system/status').then((d) => { state.status = d; schedule(); }).catch(() => {});
  render();
}
boot();
