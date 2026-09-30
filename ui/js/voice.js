// Browser voice pipeline: mic level/VAD → streaming recognition (Web Speech API) → wake word → command;
// streaming TTS by sentence; barge-in via stop words / wake word while speaking.
import { matchWakeWord, isStopIntent, speechLangFor, stripForSpeech, takeSentences } from './voice-logic.js';

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export class Voice {
  constructor({ getSettings, onCommand, onStop, onState, onInterim, onError }) {
    Object.assign(this, { getSettings, onCommand, onStop, onState, onInterim, onError });
    this.rec = null; this.active = false; this.speaking = false; this.awaitingCommand = false; this.awaitTimer = null;
    this.queue = []; this.streamBuf = {}; this.spokenTasks = new Set(); this.audio = null; this.level = () => {};
  }
  get supported() { return !!SR; }
  get ttsSupported() { return 'speechSynthesis' in window; }
  lang() { const l = this.getSettings().language; return l && l !== 'auto' ? l : (navigator.language || 'en-IN'); }

  // --- microphone level meter + energy VAD (independent of the recogniser) ---------------------------------
  async startMeter(deviceId, cb) {
    this.stopMeter(); this.level = cb;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: deviceId ? { deviceId: { exact: deviceId } } : true });
      const ctx = new (window.AudioContext || window.webkitAudioContext)(); this.actx = ctx;
      const an = ctx.createAnalyser(); an.fftSize = 512; ctx.createMediaStreamSource(this.stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const tick = () => { if (!this.stream) return; an.getByteTimeDomainData(buf); let sum = 0; for (const b of buf) sum += ((b - 128) / 128) ** 2; const rms = Math.sqrt(sum / buf.length); cb(Math.min(1, rms * 5), rms > 0.04); this.raf = requestAnimationFrame(tick); };
      tick(); return true;
    } catch (e) { this.onError(`Microphone unavailable: ${e.message}`); return false; }
  }
  stopMeter() { cancelAnimationFrame(this.raf); this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null; this.actx?.close().catch(() => {}); this.actx = null; }
  async devices() { try { await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop())); const d = await navigator.mediaDevices.enumerateDevices(); return d.filter((x) => x.kind === 'audioinput'); } catch { return []; } }

  // --- recognition ------------------------------------------------------------------------------------------------
  start(mode) {
    if (!SR) { this.onError('Speech recognition is not supported by this browser. Use Chrome or Edge, or type your commands.'); return false; }
    this.stop(); this.mode = mode; this.active = true; this.oneShot = mode === 'push_to_talk';
    const r = new SR(); this.rec = r;
    r.lang = this.lang(); r.continuous = !this.oneShot; r.interimResults = true; r.maxAlternatives = 1;
    r.onstart = () => this.onState('listening');
    r.onresult = (e) => this.handle(e);
    r.onerror = (e) => { if (!['no-speech', 'aborted'].includes(e.error)) this.onError(`Speech recognition error: ${e.error}${e.error === 'not-allowed' ? ' (microphone permission denied)' : ''}`); };
    r.onend = () => { if (this.active && !this.oneShot) { try { r.start(); } catch { /* already running */ } } else { this.active = false; this.onState('idle'); } };
    try { r.start(); } catch (e) { this.onError(e.message); return false; }
    return true;
  }
  stop() { this.active = false; clearTimeout(this.awaitTimer); this.awaitingCommand = false; try { this.rec?.abort(); } catch { /* noop */ } this.rec = null; this.onState('idle'); }

  handle(e) {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i]; const text = res[0].transcript.trim(); if (!text) continue;
      this.onInterim(text, res.isFinal);
      // Barge-in. While M.R.X. talks the mic may hear its own speaker, so only a stop word or the wake word interrupts.
      if (this.speaking) { if (isStopIntent(text) || matchWakeWord(text, this.getSettings().wake_word)) { this.cancelSpeech(); this.onStop(); } continue; }
      if (!res.isFinal) continue;
      if (isStopIntent(text)) { this.cancelSpeech(); this.onStop(); continue; }
      if (this.mode === 'push_to_talk' || this.mode === 'continuous') { this.onCommand(text); if (this.oneShot) this.stop(); continue; }
      // wake-word mode
      const w = matchWakeWord(text, this.getSettings().wake_word);
      if (w) { if (w.command) this.onCommand(w.command); else { this.awaitingCommand = true; this.onState('awaiting'); clearTimeout(this.awaitTimer); this.awaitTimer = setTimeout(() => { this.awaitingCommand = false; this.onState('listening'); }, 7000); } }
      else if (this.awaitingCommand) { this.awaitingCommand = false; clearTimeout(this.awaitTimer); this.onState('listening'); this.onCommand(text); }
    }
  }

  // --- speech output (streamed sentence by sentence) ---------------------------------------------------------
  delta(taskId, text) { const b = (this.streamBuf[taskId] || '') + text; const { sentences, rest } = takeSentences(b); this.streamBuf[taskId] = rest; if (sentences.length) this.spokenTasks.add(taskId); sentences.forEach((s) => this.say(s)); }
  final(taskId, text) { const rest = this.streamBuf[taskId]; delete this.streamBuf[taskId]; if (this.spokenTasks.has(taskId)) { this.spokenTasks.delete(taskId); if (rest) this.say(rest); } else this.say(text); }
  say(text) {
    if (!this.ttsSupported || !this.getSettings().speak_replies) return;
    const clean = stripForSpeech(text); if (!clean) return;
    this.queue.push(clean); if (!this.speaking) this.next();
  }
  next() {
    const t = this.queue.shift(); if (!t) { this.speaking = false; this.onState(this.active ? 'listening' : 'idle'); return; }
    const u = new SpeechSynthesisUtterance(t); const s = this.getSettings();
    u.lang = speechLangFor(t, this.lang()); u.rate = s.speech_rate || 1;
    const voices = speechSynthesis.getVoices();
    const pick = (s.voice_name && voices.find((v) => v.name === s.voice_name)) || voices.find((v) => v.lang === u.lang) || voices.find((v) => v.lang.startsWith(u.lang.slice(0, 2)));
    if (pick) u.voice = pick;
    u.onend = u.onerror = () => this.next();
    this.speaking = true; this.onState('speaking'); speechSynthesis.speak(u);
  }
  cancelSpeech() { this.queue = []; this.streamBuf = {}; this.spokenTasks.clear(); if (this.ttsSupported) speechSynthesis.cancel(); if (this.speaking) { this.speaking = false; this.onState(this.active ? 'listening' : 'idle'); } }
}
