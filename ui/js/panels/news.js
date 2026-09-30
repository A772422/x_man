import { h, clear, safeUrl, fmtDateTime, put } from '../dom.js';
import { api } from '../net.js';

const CATS = ['World', 'India', 'Technology', 'Science', 'Business', 'Sports', 'Entertainment', 'Security', 'Weather'];
const WX = { 0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog', 48: 'Rime fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle', 61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 80: 'Rain showers', 95: 'Thunderstorm' };

export function create(ctx) {
  const root = ctx.root; let cat = 'World'; let data = null; let error = null; let loading = false; let wx = null; let wxq = '';
  async function load() {
    loading = true; error = null; render();
    try {
      if (cat === 'Weather') { /* on demand */ } else data = await api('/api/news?category=' + cat + '&limit=25');
    } catch (e) { error = e.data?.error || e.message; data = null; }
    loading = false; render();
  }
  async function weather(lat, lon, label) {
    try { const d = await api(`/api/maps/weather?lat=${lat}&lon=${lon}`); wx = { ...d, label }; error = null; } catch (e) { error = e.data?.error || e.message; wx = null; }
    render();
  }
  async function findPlace() {
    if (!wxq.trim()) return;
    try { const g = await api('/api/maps/geocode?query=' + encodeURIComponent(wxq)); const p = g.results[0]; if (!p) { error = 'Place not found'; render(); return; } weather(p.lat, p.lon, p.name); } catch (e) { error = e.data?.error || e.message; render(); }
  }
  function here() { navigator.geolocation ? navigator.geolocation.getCurrentPosition((p) => weather(p.coords.latitude, p.coords.longitude, 'Your location'), (e) => { error = 'Location permission denied: search for a place instead.'; render(); }) : (error = 'Geolocation is not supported'); render(); }
  function render() {
    clear(root);
    put(root, h('h2', {}, 'Live news'), h('p', { class: 'sub' }, 'Headlines come straight from each publisher’s feed, with source, publication time and link. M.R.X. does not verify claims or add its own.'),
      h('div', { class: 'tabs' }, ...CATS.map((c) => h('button', { class: c === cat ? 'active' : '', onclick: () => { cat = c; c === 'Weather' ? render() : load(); } }, c))));
    if (cat === 'Weather') {
      const inp = h('input', { type: 'text', class: 'grow', placeholder: 'City or place…', value: wxq }); inp.addEventListener('input', () => { wxq = inp.value; }); inp.addEventListener('keydown', (e) => e.key === 'Enter' && findPlace());
      put(root, h('div', { class: 'row', style: 'margin-bottom:10px' }, inp, h('button', { class: 'btn', onclick: findPlace }, 'Look up'), h('button', { class: 'btn', onclick: here }, '📍 My location')));
      if (error) put(root, h('div', { class: 'banner err' }, 'Weather is unavailable: ' + error));
      if (wx) { const c = wx.current, u = wx.units; put(root, h('div', { class: 'card' }, h('h3', {}, wx.label), h('div', { style: 'font-size:32px;font-weight:300' }, `${c.temperature_2m}${u.temperature_2m}`), h('div', {}, WX[c.weather_code] || `code ${c.weather_code}`),
        h('div', { class: 'muted' }, `Feels like ${c.apparent_temperature}${u.apparent_temperature} · humidity ${c.relative_humidity_2m}% · wind ${c.wind_speed_10m} ${u.wind_speed_10m} · precipitation ${c.precipitation} ${u.precipitation}`),
        h('div', { class: 'attrib' }, `Provider: ${wx.provider} · data time: ${wx.data_updated} · retrieved: ${fmtDateTime(Date.parse(wx.retrieved_at) / 1000)}`))); }
      else if (!error) put(root, h('div', { class: 'empty' }, 'Search a place or use your location to fetch live weather.'));
      return;
    }
    if (loading) { put(root, h('div', { class: 'empty' }, 'Fetching live headlines…')); return; }
    if (error) { put(root, h('div', { class: 'banner err' }, /unavailable/i.test(error) ? error : `The news provider is currently unavailable. ${error}`), h('button', { class: 'btn', onclick: load }, 'Retry')); return; }
    if (!data) { put(root, h('div', { class: 'empty' }, h('button', { class: 'btn primary', onclick: load }, 'Load headlines'))); return; }
    put(root, h('div', { class: 'card' }, ...data.articles.map((a) => { const u = safeUrl(a.link);
      return h('div', { class: 'article' }, u ? h('a', { class: 't', href: u, target: '_blank', rel: 'noopener noreferrer' }, a.title) : h('span', { class: 't' }, a.title),
        a.summary ? h('div', { class: 'muted', style: 'font-size:12.5px' }, a.summary) : null,
        h('div', { class: 'm' }, h('b', {}, a.source), h('span', {}, a.published ? `published ${fmtDateTime(Date.parse(a.published) / 1000)}` : 'publication time not provided'),
          h('span', { class: 'pill ' + (a.freshness.startsWith('current') ? 'ok' : a.freshness === 'older article' ? 'warn' : '') }, a.freshness), h('span', { class: 'pill' }, 'claims: not independently verified'))); }),
      h('div', { class: 'attrib' }, `Retrieved: ${fmtDateTime(Date.parse(data.retrieved_at) / 1000)} · Providers: ${data.providers.map((p) => p.ok ? `${p.source} ✓` : `${p.source} ✗ (${p.error})`).join(' · ')}`)));
  }
  return { show() { data ? render() : load(); }, update() {} };
}
