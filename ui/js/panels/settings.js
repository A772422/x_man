import { h, clear, put } from '../dom.js';
import { api } from '../net.js';
import { LANGS } from '../voice-logic.js';

const setPath = (o, path, v) => { const ks = path.split('.'); let c = o; ks.slice(0, -1).forEach((k) => { c = c[k] ??= {}; }); c[ks.at(-1)] = v; return o; };
const getPath = (o, path) => path.split('.').reduce((c, k) => (c == null ? undefined : c[k]), o);

export function create(ctx) {
  const root = ctx.root; let cfg = null; let secrets = null; let tools = []; let plugins = []; let devices = []; let sec = ''; let mics = [];
  let section = 'AI';
  async function load() {
    try { [cfg, secrets, { tools, plugins }] = await Promise.all([api('/api/settings').then((d) => d.settings), api('/api/secrets'), api('/api/tools')]); devices = (await api('/api/network/devices')).known; } catch (e) { ctx.toast(e.message, 'err'); }
    mics = await ctx.voice.devices(); render();
  }
  async function save(path, value, msg = 'Saved') { const patch = setPath({}, path, value); try { cfg = (await api('/api/settings', { method: 'PATCH', body: patch })).settings; ctx.settingsChanged(cfg); ctx.toast(msg, 'ok'); } catch (e) { ctx.toast(e.message, 'err'); } render(); }
  const field = (label, path, kind, opts = {}) => {
    const v = getPath(cfg, path); let input;
    if (kind === 'bool') input = h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: !!v, onchange: (e) => save(path, e.target.checked) }), opts.text || 'Enabled');
    else if (kind === 'select') input = h('select', { onchange: (e) => save(path, e.target.value) }, ...opts.options.map(([val, lab]) => h('option', { value: val, selected: val === v }, lab)));
    else if (kind === 'number') input = h('input', { type: 'number', value: v, min: opts.min, max: opts.max, step: opts.step || 1, onchange: (e) => save(path, Number(e.target.value)) });
    else input = h('input', { type: 'text', value: v ?? '', placeholder: opts.placeholder, list: opts.list, onchange: (e) => save(path, e.target.value) });
    return h('label', { class: 'field' }, label, input, opts.help ? h('span', { class: 'dim' }, opts.help) : null);
  };
  const S = {
    AI: () => [field('Model', 'ai.model', 'text', { list: 'models', help: 'Claude model ID used by the agent (requires ANTHROPIC_API_KEY).' }), h('datalist', { id: 'models' }, ...['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-fable-5-1', 'claude-haiku-4-5'].map((m) => h('option', { value: m }))),
      field('Reasoning effort', 'ai.effort', 'select', { options: [['', 'model default'], ['low', 'low'], ['medium', 'medium'], ['high', 'high'], ['xhigh', 'xhigh'], ['max', 'max']], help: 'Leave on “model default” for models that do not support effort (e.g. Haiku).' }),
      field('Max output tokens', 'ai.max_tokens', 'number', { min: 1024, max: 128000 }), field('Max agent steps per task', 'ai.max_steps', 'number', { min: 1, max: 50, help: 'Hard limit that prevents runaway loops.' }),
      field('Streaming', 'ai.streaming', 'bool', { text: 'Stream responses (always on in this build)' }),
      h('p', { class: 'dim' }, 'Temperature and context length are managed by the model itself; current Claude models do not accept sampling overrides.'),
      h('div', { class: 'muted' }, `Provider: Anthropic · engine now: ${ctx.state.status?.engine === 'llm' ? 'AI engine online' : 'offline command engine'}`),
      h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: async () => { ctx.toast('Testing…'); try { const r = await api('/api/ai/test', { method: 'POST' }); r.ok ? ctx.toast(`AI engine works (${r.model}, ${r.ms} ms)`, 'ok', 6000) : ctx.toast(`Problem: ${r.error}`, 'err', 12000); } catch (e) { ctx.toast(e.message, 'err'); } ctx.refreshStatus(); } }, 'Test AI connection'))],
    Voice: () => [field('Input mode', 'voice.mode', 'select', { options: [['push_to_talk', 'Push-to-talk (click 🎤 / Ctrl+Space)'], ['wake_word', 'Wake word'], ['continuous', 'Continuous']] }),
      field('Recognition language', 'voice.language', 'select', { options: LANGS }), field('Wake word', 'voice.wake_word', 'text', { help: 'Default “hey mrx”. Several spellings recognisers produce are accepted.' }),
      field('Speech speed', 'voice.speech_rate', 'number', { min: 0.5, max: 2, step: 0.1 }), field('Speak replies aloud', 'voice.speak_replies', 'bool', { text: 'Text-to-speech on' }),
      field('Voice', 'voice.voice_name', 'select', { options: [['', 'automatic (by reply language)'], ...(speechSynthesis?.getVoices?.() || []).map((v) => [v.name, `${v.name} (${v.lang})`])] }),
      h('label', { class: 'field' }, 'Microphone (level meter & voice-activity indicator)', h('select', { onchange: (e) => ctx.voice.startMeter(e.target.value, ctx.meter) }, h('option', { value: '' }, 'system default'), ...mics.map((m) => h('option', { value: m.deviceId }, m.label || 'microphone')))),
      h('p', { class: 'dim' }, 'Speech recognition and synthesis run in your browser (Chrome/Edge recommended) and always use the system default microphone/speaker; browsers do not let pages route them to other devices. Audio is processed by your browser’s speech provider.')],
    Automation: () => [field('Retry limit (transient failures)', 'automation.retry_limit', 'number', { min: 0, max: 5, help: 'Bounded: never retries forever.' }), field('Parallel execution', 'automation.parallel_execution', 'bool', { text: 'Run independent tool calls concurrently' }),
      field('Auto-execution', 'automation.auto_execute', 'bool', { text: 'Run state-changing actions without asking (off = ask for everything except reads)' }),
      field('Confirmation timeout (s)', 'automation.confirmation_timeout_s', 'number', { min: 10, max: 900 }),
      h('h3', {}, 'Confirmation policy per tool'), h('p', { class: 'dim' }, 'Level 1 runs automatically · 2 asks first (can be marked trusted) · 3 always asks. Overrides apply to every future call.'),
      h('div', { class: 'card', style: 'max-height:340px;overflow:auto' }, h('table', {}, h('thead', {}, h('tr', {}, ['tool', 'level', 'trusted (level 2)'].map((c) => h('th', {}, c)))), h('tbody', {}, ...tools.filter((t) => t.risk > 1 || (cfg.automation.confirmation_overrides || {})[t.name]).map((t) => {
        const ov = (cfg.automation.confirmation_overrides || {})[t.name]; const trusted = (cfg.automation.trusted_tools || []).includes(t.name);
        return h('tr', {}, h('td', { title: t.description }, t.name), h('td', {}, h('select', { onchange: (e) => { const o = { ...(cfg.automation.confirmation_overrides || {}) }; e.target.value === '' ? delete o[t.name] : (o[t.name] = Number(e.target.value)); save('automation.confirmation_overrides', o); } }, ...[['', `default (${t.risk})`], ['1', '1 automatic'], ['2', '2 confirm'], ['3', '3 always']].map(([v, l]) => h('option', { value: v, selected: String(ov ?? '') === v }, l)))),
          h('td', {}, t.risk === 2 ? h('input', { type: 'checkbox', checked: trusted, onchange: (e) => { const l = new Set(cfg.automation.trusted_tools || []); e.target.checked ? l.add(t.name) : l.delete(t.name); save('automation.trusted_tools', [...l]); } }) : '—')); })))) ],
    Browser: () => [field('Browser channel', 'browser.channel', 'select', { options: [['', 'Playwright Chromium'], ['chrome', 'Google Chrome (installed)'], ['msedge', 'Microsoft Edge (installed)']] }), field('Headless', 'browser.headless', 'bool', { text: 'Hide the browser window' }),
      field('Persistent profile', 'browser.persistent_profile', 'bool', { text: 'Keep cookies/logins between runs' }), field('Download folder', 'browser.download_dir', 'text', { placeholder: 'default: ~/Downloads' }),
      field('Attach to existing browser (CDP URL)', 'browser.cdp_url', 'text', { placeholder: 'http://127.0.0.1:9222', help: 'Start Chrome with --remote-debugging-port=9222 to control your own browser.' }), h('p', { class: 'dim' }, 'Browser settings apply the next time the automation browser is launched.')],
    Memory: () => [field('Memory', 'memory.enabled', 'bool', { text: 'Remember things across sessions' }), h('div', { class: 'row' }, h('button', { class: 'btn', onclick: () => ctx.go('memory') }, 'View / edit memories'),
      h('button', { class: 'btn danger', onclick: async () => { if (confirm('Clear all conversations, task history and steps? Memories are kept.')) { await api('/api/history', { method: 'DELETE' }); ctx.toast('History cleared', 'ok'); ctx.clearLocal(); } } }, 'Clear history'))],
    Security: () => [h('p', { class: 'muted' }, `Secret storage backend: ${secrets?.backend}. Values are write-only here — they are never shown again, never written to settings/DB/logs, and never sent to the AI model.`),
      h('div', { class: 'card' }, h('table', {}, h('tbody', {}, ...Object.entries(secrets?.secrets || {}).map(([n, s]) => h('tr', {}, h('td', { class: 'mono' }, n), h('td', {}, h('span', { class: 'pill ' + (s.set ? 'ok' : '') }, s.set ? `set (${s.source})` : 'not set')),
        h('td', {}, s.source === 'none' || /keychain|file/.test(s.source) ? [h('button', { class: 'btn small', onclick: () => ctx.askSecret(n) }, s.set ? 'Replace' : 'Set'), ' ', s.set ? h('button', { class: 'btn small danger', onclick: async () => { await api('/api/secrets/' + n, { method: 'DELETE' }); load(); ctx.refreshStatus(); } }, 'Remove') : null] : h('span', { class: 'dim' }, 'from environment')))))),),
      h('h3', {}, 'Plugins & permissions'), h('div', { class: 'card' }, h('table', {}, h('thead', {}, h('tr', {}, ['plugin', 'version', 'permissions', 'enabled'].map((c) => h('th', {}, c)))), h('tbody', {}, ...plugins.map((p) => h('tr', {}, h('td', { title: p.description }, p.name, p.builtin ? '' : h('span', { class: 'pill warn', style: 'margin-left:6px' }, 'external')), h('td', {}, p.version), h('td', { class: 'muted' }, p.permissions.join(', ')),
        h('td', {}, h('input', { type: 'checkbox', checked: p.enabled, onchange: async (e) => { await api(`/api/plugins/${p.name}/${e.target.checked ? 'enable' : 'disable'}`, { method: 'POST' }); load(); } }))))))),
      field('Email auto-send', 'email.auto_send', 'bool', { text: 'Send emails without asking (default OFF — recommended: draft → review → send)' })],
    Network: () => [field('Automatic scan interval (seconds, 0 = manual)', 'network.scan_interval_s', 'number', { min: 0, max: 86400 }),
      h('div', { class: 'field' }, 'Discovery methods', h('div', { class: 'row' }, ...['arp', 'ping', 'ssdp', 'mdns'].map((m) => h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: cfg.network.methods.includes(m), onchange: (e) => { const s = new Set(cfg.network.methods); e.target.checked ? s.add(m) : s.delete(m); save('network.methods', [...s]); } }), m)))),
      h('h3', {}, 'Device names'), devices.length ? h('div', { class: 'card' }, h('table', {}, h('tbody', {}, ...devices.map((d) => { const key = d.mac.startsWith('ip:') ? d.ip : d.mac; return h('tr', {}, h('td', { class: 'mono' }, `${d.ip} · ${d.mac}`), h('td', {}, h('input', { type: 'text', value: cfg.network.device_names[key] || '', placeholder: d.hostname || 'name…', onchange: (e) => save('network.device_names', { ...cfg.network.device_names, [key]: e.target.value }) }))); })))) : h('div', { class: 'dim' }, 'Scan first to name discovered devices.')],
  };
  function render() {
    clear(root); if (!cfg) { put(root, h('div', { class: 'empty' }, 'Loading settings…')); return; }
    put(root, h('h2', {}, 'Settings'), h('div', { class: 'tabs' }, ...Object.keys(S).map((k) => h('button', { class: k === section ? 'active' : '', onclick: () => { section = k; render(); } }, k))),
      h('div', { class: 'card', style: 'display:grid;gap:14px;max-width:760px' }, ...S[section]()));
  }
  return { show: load, update() {} };
}
