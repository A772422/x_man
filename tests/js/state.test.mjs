import test from 'node:test';
import assert from 'node:assert/strict';
import { initialState, reduce, avatarState } from '../../ui/js/state.js';

const ev = (type, data = {}, extra = {}) => ({ type, data, ts: 1000, ...extra });
const task = (o = {}) => ({ id: 't1', command: 'open chrome', status: 'RUNNING', outcome: 'EXECUTING', progress: 0.3, steps: [], ...o });

test('task lifecycle updates: started → step → completed', () => {
  const s = initialState();
  reduce(s, ev('agent.started', task()));
  reduce(s, ev('task.step', { task_id: 't1', step: { idx: 0, title: 'open chrome', status: 'running', detail: '' } }));
  reduce(s, ev('task.step', { task_id: 't1', step: { idx: 0, title: 'open chrome', status: 'done', detail: 'process detected' } }));
  reduce(s, ev('task.updated', task({ status: 'COMPLETED', outcome: 'COMPLETED', progress: 1 })));
  assert.equal(s.order.length, 1);
  assert.equal(s.tasks.t1.steps.length, 1);          // step updated in place, not duplicated
  assert.equal(s.tasks.t1.steps[0].status, 'done');
  assert.equal(s.tasks.t1.status, 'COMPLETED');
  assert.equal(s.flash.state, 'success');
});

test('failure and partial completion map to error / warning avatar states', () => {
  const s = initialState(); const now = 5000;
  reduce(s, ev('task.updated', task({ status: 'FAILED', outcome: 'FAILED', error: 'exe not found' })), now);
  assert.equal(avatarState(s, now), 'error');
  assert.match(s.activity.at(-1).detail, /exe not found/);
  reduce(s, ev('task.updated', task({ id: 't2', status: 'COMPLETED', outcome: 'PARTIALLY_COMPLETED' })), now);
  assert.equal(avatarState(s, now), 'warning');
  assert.equal(avatarState(s, now + 10000), 'idle');   // flash expires
});

test('avatar priority: speaking > confirmation > executing > thinking > listening', () => {
  const s = initialState();
  assert.equal(avatarState(s), 'idle');
  s.listening = true; assert.equal(avatarState(s), 'listening');
  reduce(s, ev('agent.thinking', { task_id: 't1' })); assert.equal(avatarState(s), 'thinking');
  reduce(s, ev('tool.started', { tool: 'create_folder', arguments: { path: 'x' } })); assert.equal(avatarState(s), 'executing');
  reduce(s, ev('confirm.required', { id: 'c1', tool: 'delete_file', level: 2, arguments: {} })); assert.equal(avatarState(s), 'warning');
  s.speaking = true; assert.equal(avatarState(s), 'speaking');
  reduce(s, ev('confirm.resolved', { id: 'c1' }));
  assert.equal(s.confirmations.length, 0);
});

test('tool events: verified vs unverified vs failed are distinguished in the console', () => {
  const s = initialState();
  reduce(s, ev('tool.started', { tool: 'a', arguments: {} }));
  reduce(s, ev('tool.completed', { tool: 'a', verified: true, verification: 'folder exists', ms: 4 }));
  reduce(s, ev('tool.completed', { tool: 'b', verified: null, ms: 2 }));
  reduce(s, ev('tool.failed', { tool: 'c', error: 'nope', ms: 1 }));
  assert.deepEqual(s.activity.slice(-3).map((a) => a.kind), ['ok', 'warn', 'err']);
  assert.equal(s.running, 0);
});

test('streaming text accumulates then is replaced by the final message', () => {
  const s = initialState();
  reduce(s, ev('agent.text_delta', { task_id: 't1', text: 'Open' }));
  reduce(s, ev('agent.text_delta', { task_id: 't1', text: 'ing…' }));
  assert.equal(s.streaming.t1, 'Opening…');
  reduce(s, ev('agent.message', { task_id: 't1', text: 'Opened Chrome.' }));
  assert.equal(s.streaming.t1, undefined);
  assert.equal(s.messages.at(-1).text, 'Opened Chrome.');
});

test('system stats keep a bounded history; alerts are recorded', () => {
  const s = initialState();
  for (let i = 0; i < 100; i++) reduce(s, ev('system.stats', { cpu_percent: i, ram: { percent: 50 }, net: { up_bps: 1, down_bps: 2 } }));
  assert.equal(s.hist.cpu.length, 60); assert.equal(s.hist.cpu.at(-1), 99);
  reduce(s, ev('system.alert', { kind: 'ram', message: 'RAM usage is 95%' }));
  assert.equal(s.notices.at(-1).text, 'RAM usage is 95%');
});

test('network scan progress events drive scan state', () => {
  const s = initialState();
  reduce(s, ev('network.scan_started', { networks: [] })); assert.equal(s.scan.running, true);
  reduce(s, ev('network.device_found', { ip: '192.168.1.5', method: 'arp' })); reduce(s, ev('network.device_found', { ip: '192.168.1.6', method: 'arp' }));
  assert.equal(s.scan.found, 2);
  reduce(s, ev('network.scan_completed', { count: 2, methods: ['arp'] })); assert.equal(s.scan.running, false);
  reduce(s, ev('network.scan_failed', { error: 'no interface' })); assert.equal(s.scan.error, 'no interface');
});

test('websocket hello hydrates tasks and pending confirmations; ui.navigate is surfaced', () => {
  const s = initialState();
  reduce(s, ev('hello', { status: { engine: 'local' }, tasks: [task({ id: 'b' }), task({ id: 'a' })], pending_confirmations: [{ id: 'c9' }] }));
  assert.equal(s.status.engine, 'local'); assert.equal(s.order.length, 2); assert.equal(s.confirmations[0].id, 'c9');
  reduce(s, ev('ui.navigate', { panel: 'map' })); assert.equal(s.navigate.panel, 'map');
});

test('activity log is bounded', () => {
  const s = initialState();
  for (let i = 0; i < 500; i++) reduce(s, ev('browser.navigation', { url: 'http://x/' + i }));
  assert.equal(s.activity.length, 300);
});
