import { h, clear, safeUrl, put } from '../dom.js';
import { tool, api } from '../net.js';

// Built-in player using the official YouTube IFrame Player API. Real player state is reported back to the
// backend over the WebSocket; that report is what "playback verified" is based on.
export function create(ctx) {
  const root = ctx.root; let player = null; let apiReady = null; let results = []; let idx = -1; let meta = null; let history = []; let timer = null; let pendingLoad = null; let msg = '';
  const elPlayer = h('div', { class: 'yt-player' }, h('div', { id: 'yt-frame' }));
  const info = h('div', { class: 'card' }); const list = h('div', {}); const status = h('div', { class: 'muted mono', style: 'font-size:12px;margin-top:6px' });
  const seek = h('input', { type: 'range', min: 0, max: 100, value: 0, class: 'grow', 'aria-label': 'Seek' }); const vol = h('input', { type: 'range', min: 0, max: 100, value: 80, 'aria-label': 'Volume' });
  const q = h('input', { type: 'text', class: 'grow', placeholder: 'Search YouTube, or paste a video URL / ID…' });
  let seeking = false;

  function loadApi() {
    if (apiReady) return apiReady;
    apiReady = new Promise((res, rej) => {
      if (window.YT?.Player) return res();
      window.onYouTubeIframeAPIReady = () => res();
      const s = document.createElement('script'); s.src = 'https://www.youtube.com/iframe_api'; s.onerror = () => rej(new Error('Could not load the YouTube player (offline?)')); document.head.append(s);
    });
    return apiReady;
  }
  const codes = { '-1': 'unstarted', 0: 'ended', 1: 'playing', 2: 'paused', 3: 'buffering', 5: 'cued' };
  function report(extra = {}) {
    if (!player?.getPlayerState) return;
    const st = codes[player.getPlayerState()] || 'unknown';
    const d = { video_id: player.getVideoData?.().video_id, state: st, time: player.getCurrentTime?.() || 0, duration: player.getDuration?.() || 0, volume: player.getVolume?.() ?? 0, muted: player.isMuted?.() || false, title: player.getVideoData?.().title, ...extra };
    ctx.send({ type: 'youtube.state', data: d });
    status.textContent = `state: ${d.state} · ${Math.floor(d.time)}s / ${Math.floor(d.duration)}s · volume ${d.volume}`;
    if (!seeking && d.duration) { seek.max = d.duration; seek.value = d.time; }
  }
  async function ensurePlayer(videoId) {
    try { await loadApi(); } catch (e) { msg = e.message; ctx.toast(e.message, 'err'); ctx.send({ type: 'youtube.state', data: { state: 'error', error: e.message } }); render(); return false; }
    if (player) return true;
    await new Promise((res) => { player = new YT.Player('yt-frame', { videoId, playerVars: { autoplay: 1, rel: 0, playsinline: 1 }, events: {
      onReady: () => { res(); if (pendingLoad) { player.loadVideoById(pendingLoad); pendingLoad = null; } },
      onStateChange: () => { report(); if (player.getPlayerState() === 0 && idx >= 0 && idx < results.length - 1) playIndex(idx + 1); },
      onError: (e) => { const m = { 2: 'invalid video id', 5: 'HTML5 player error', 100: 'video not found/private', 101: 'embedding disabled by the owner', 150: 'embedding disabled by the owner' }[e.data] || `player error ${e.data}`; msg = `YouTube: ${m}`; ctx.toast(msg, 'warn'); ctx.send({ type: 'youtube.state', data: { video_id: player.getVideoData?.().video_id, state: 'error', error: m } }); render(); } } }); });
    clearInterval(timer); timer = setInterval(() => player?.getPlayerState?.() === 1 && report(), 1000);
    return true;
  }
  async function load(videoId, m, autoplay = true) {
    meta = m || null; msg = '';
    if (!(await ensurePlayer(videoId))) return;
    if (player.loadVideoById) autoplay ? player.loadVideoById(videoId) : player.cueVideoById(videoId); else pendingLoad = videoId;
    render();
  }
  const playIndex = (i) => { idx = i; const r = results[i]; load(r.video_id, r); };
  async function search(text) {
    text = text.trim(); if (!text) return;
    const idm = text.match(/(?:v=|youtu\.be\/|embed\/|shorts\/)([\w-]{11})/) || (/^[\w-]{11}$/.test(text) ? [null, text] : null);
    if (idm) { load(idm[1], null); return; }
    const r = await tool('youtube_search', { query: text });
    if (!r.success) { msg = r.error; ctx.toast(r.error, 'warn'); render(); return; }
    ingest(r.result);
  }
  function ingest(res) { results = res.results || []; history = [res.query, ...history.filter((x) => x !== res.query)].slice(0, 8); idx = -1; msg = ''; render(); }
  function command(c) {
    if (!player) return;
    const a = c.action, v = c.value;
    if (a === 'play') player.playVideo(); else if (a === 'pause') player.pauseVideo(); else if (a === 'seek') player.seekTo(v, true);
    else if (a === 'volume') { player.setVolume(v); player.unMute?.(); } else if (a === 'fullscreen') player.getIframe().requestFullscreen?.().catch(() => ctx.toast('Fullscreen was blocked by the browser', 'warn'));
    else if (a === 'next' && idx < results.length - 1) playIndex(idx + 1); else if (a === 'previous' && idx > 0) playIndex(idx - 1);
    setTimeout(report, 300);
  }
  seek.addEventListener('input', () => { seeking = true; }); seek.addEventListener('change', () => { player?.seekTo(+seek.value, true); seeking = false; setTimeout(report, 200); });
  vol.addEventListener('input', () => player?.setVolume(+vol.value));
  q.addEventListener('keydown', (e) => e.key === 'Enter' && search(q.value));

  function render() {
    clear(root); const cur = meta || (idx >= 0 ? results[idx] : null);
    put(clear(info), h('h3', {}, 'Now playing'), cur ? [h('div', { style: 'font-weight:600' }, cur.title || '—'), h('div', { class: 'muted' }, [cur.channel, cur.views ? `${Number(cur.views).toLocaleString()} views` : null, cur.duration].filter(Boolean).join(' · '))] : h('div', { class: 'muted' }, 'Nothing loaded'), status);
    put(clear(list), ...(results.length ? results.map((r, i) => h('div', { class: 'vid' + (i === idx ? ' cur' : ''), onclick: () => playIndex(i) }, safeUrl(r.thumbnail) ? h('img', { src: r.thumbnail, alt: '' }) : h('div', { class: 'ph' }), h('div', {}, h('div', { class: 't' }, `${i + 1}. ${r.title}`), h('div', { class: 'c' }, `${r.channel}${r.duration ? ' · ' + r.duration : ''}`)))) : [h('div', { class: 'empty' }, 'No results yet')]));
    put(root, h('h2', {}, 'YouTube player'), h('p', { class: 'sub' }, 'Search via the official YouTube Data API (needs YOUTUBE_API_KEY) or paste a video URL/ID. Playback uses the official embedded player.'),
      msg ? h('div', { class: 'banner' }, msg) : null,
      h('div', { class: 'row', style: 'margin-bottom:10px' }, q, h('button', { class: 'btn primary', onclick: () => search(q.value) }, 'Search / Play')),
      history.length ? h('div', { class: 'tabs' }, ...history.map((x) => h('button', { onclick: () => { q.value = x; search(x); } }, x))) : null,
      h('div', { class: 'yt-grid' }, h('div', {}, elPlayer, h('div', { class: 'row', style: 'margin:8px 0' },
        h('button', { class: 'btn small', onclick: () => command({ action: 'previous' }) }, '⏮'), h('button', { class: 'btn small', onclick: () => command({ action: 'play' }) }, '▶'), h('button', { class: 'btn small', onclick: () => command({ action: 'pause' }) }, '❚❚'), h('button', { class: 'btn small', onclick: () => command({ action: 'next' }) }, '⏭'), seek, h('span', { class: 'muted' }, '🔊'), vol, h('button', { class: 'btn small', onclick: () => command({ action: 'fullscreen' }) }, '⛶')), info),
        h('div', { class: 'card' }, h('h3', {}, 'Results / playlist'), list)));
    root.querySelector('.yt-player')?.replaceWith(elPlayer);
  }
  return { show() { render(); }, update() {},
    event(ev) {
      if (ev.type === 'youtube.results') ingest(ev.data);
      else if (ev.type === 'youtube.load') { const i = results.findIndex((r) => r.video_id === ev.data.video_id); idx = i; load(ev.data.video_id, ev.data.meta && ev.data.meta.title ? ev.data.meta : null, ev.data.autoplay !== false); }
      else if (ev.type === 'youtube.command') command(ev.data);
    } };
}
