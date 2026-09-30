import { h, clear, fmtDateTime, safeUrl } from '../dom.js';
import { api } from '../net.js';

// Real map: Leaflet + OpenStreetMap tiles. Dynamic layers are fetched from public APIs and carry provider + timestamps.
export function create(ctx) {
  const root = ctx.root; let map = null; const groups = {}; let notes = {}; let layers = null; let baseFailed = false;
  const wanted = { earthquakes: false, flights: false, weather: true }; let period = '2.5_day'; let banner = ''; let meta = h('div', { class: 'attrib' });
  const mapEl = h('div', { id: 'map' }); const bar = h('div', { class: 'layer-bar' }); const bnr = h('div');

  function ensureMap() {
    if (map) return true;
    if (!window.L) { bnr.replaceChildren(h('div', { class: 'banner err' }, 'Live map data is unavailable: the map library could not be loaded.')); return false; }
    map = L.map(mapEl, { worldCopyJump: true, minZoom: 2 }).setView([20, 20], 2);
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '© OpenStreetMap contributors' }).addTo(map);
    tiles.on('tileerror', () => { if (!baseFailed) { baseFailed = true; bnr.replaceChildren(h('div', { class: 'banner' }, 'Map tiles could not be loaded (offline?). Live layers may also be unavailable.')); } });
    tiles.on('tileload', () => { if (baseFailed) { baseFailed = false; bnr.replaceChildren(); } });
    map.on('click', (e) => wanted.weather && weather(e.latlng.lat, e.latlng.lng));
    map.on('moveend', () => wanted.flights && loadFlights());
    for (const k of ['earthquakes', 'flights', 'weather', 'search']) groups[k] = L.layerGroup().addTo(map);
    return true;
  }
  const setNote = (k, txt) => { notes[k] = txt; meta.replaceChildren(...Object.values(notes).filter(Boolean).map((t) => h('div', {}, t))); };
  async function fail(k, e) { groups[k]?.clearLayers(); setNote(k, `${k}: live data unavailable — ${e.data?.error || e.message}`); bnr.replaceChildren(h('div', { class: 'banner err' }, `Live ${k} data is unavailable: ${e.data?.error || e.message}`)); }
  const stamp = (d) => `${d.provider} · data updated ${String(d.data_updated).replace('T', ' ').slice(0, 19)} · retrieved ${fmtDateTime(Date.parse(d.retrieved_at) / 1000)}`;

  async function loadQuakes() {
    groups.earthquakes.clearLayers(); if (!wanted.earthquakes) { setNote('earthquakes', ''); return; }
    try { const d = await api('/api/maps/earthquakes?period=' + period); bnr.replaceChildren();
      d.features.forEach((f) => L.circleMarker([f.lat, f.lon], { radius: Math.max(3, (f.mag || 1) * 2.4), color: '#ff5d6c', weight: 1, fillOpacity: .35 }).bindPopup(`<b>M ${f.mag ?? '?'}</b><br>${esc(f.place || '')}<br>depth ${f.depth_km} km<br>${new Date(f.time).toLocaleString()}${safeUrl(f.url) ? `<br><a href="${safeUrl(f.url)}" target="_blank" rel="noopener noreferrer">USGS details</a>` : ''}`).addTo(groups.earthquakes));
      setNote('earthquakes', `Earthquakes (${d.count}): ${stamp(d)}`); } catch (e) { fail('earthquakes', e); }
  }
  async function loadFlights() {
    groups.flights.clearLayers(); if (!wanted.flights) { setNote('flights', ''); return; }
    if (map.getZoom() < 5) { setNote('flights', 'Flights: zoom in to level 5+ to load aircraft in view (anonymous API limit).'); return; }
    const b = map.getBounds();
    try { const d = await api(`/api/maps/flights?south=${b.getSouth()}&west=${b.getWest()}&north=${b.getNorth()}&east=${b.getEast()}`); bnr.replaceChildren();
      d.features.forEach((f) => L.circleMarker([f.lat, f.lon], { radius: 4, color: '#3ee6ff', weight: 1, fillOpacity: .8 }).bindTooltip(`${esc(f.callsign || 'n/a')} · ${esc(f.country || '')} · ${f.alt_m != null ? Math.round(f.alt_m) + ' m' : ''}`).addTo(groups.flights));
      setNote('flights', `Flights (${d.count}): ${stamp(d)}`); } catch (e) { fail('flights', e); }
  }
  async function weather(lat, lon) {
    try { const d = await api(`/api/maps/weather?lat=${lat}&lon=${lon}`); bnr.replaceChildren(); const c = d.current, u = d.units; groups.weather.clearLayers();
      L.marker([d.lat, d.lon]).addTo(groups.weather).bindPopup(`<b>${c.temperature_2m}${u.temperature_2m}</b><br>humidity ${c.relative_humidity_2m}% · wind ${c.wind_speed_10m} ${u.wind_speed_10m}<br>${d.provider}, ${esc(String(d.data_updated))}`).openPopup();
      setNote('weather', `Weather: ${stamp(d)}`); } catch (e) { fail('weather', e); }
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  async function search(q) {
    if (!q.trim()) return;
    try { const d = await api('/api/maps/geocode?query=' + encodeURIComponent(q)); groups.search.clearLayers(); const p = d.results[0]; if (!p) { ctx.toast('Place not found', 'warn'); return; }
      map.fitBounds([[p.boundingbox[0], p.boundingbox[2]], [p.boundingbox[1], p.boundingbox[3]]]); L.marker([p.lat, p.lon]).addTo(groups.search).bindPopup(`${esc(p.name)}<br>${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`).openPopup(); setNote('search', `Search: ${d.provider} · retrieved ${fmtDateTime(Date.parse(d.retrieved_at) / 1000)}`);
    } catch (e) { fail('search', e); }
  }
  function build() {
    const q = h('input', { type: 'text', class: 'grow', placeholder: 'Search a place, city or country…' }); q.addEventListener('keydown', (e) => e.key === 'Enter' && search(q.value));
    const sw = (k, label, fn) => h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: wanted[k], onchange: (e) => { wanted[k] = e.target.checked; fn?.(); if (k === 'weather' && !wanted.weather) { groups.weather.clearLayers(); setNote('weather', ''); } } }), label);
    const per = h('select', { onchange: (e) => { period = e.target.value; loadQuakes(); } }, ...['all_hour', 'all_day', '2.5_day', '4.5_week'].map((p) => h('option', { value: p, selected: p === period }, p.replace('_', ' '))));
    const un = layers ? Object.entries(layers.unavailable).map(([k, v]) => h('span', { class: 'pill', title: v }, `${k}: unavailable`)) : [];
    bar.replaceChildren(sw('earthquakes', 'Earthquakes (USGS)', loadQuakes), per, sw('flights', 'Flights (OpenSky)', loadFlights), sw('weather', 'Weather on click (Open-Meteo)'), ...un);
    root.replaceChildren(h('h2', {}, 'Live world map'), h('p', { class: 'sub' }, 'Real OpenStreetMap geography. Each live layer names its provider and update time; if a provider fails the layer says so instead of showing stand-in data.'),
      h('div', { class: 'row', style: 'margin-bottom:8px' }, q, h('button', { class: 'btn', onclick: () => search(q.value) }, 'Find')), bar, bnr, mapEl, meta);
  }
  return { async show() { if (!layers) { try { layers = await api('/api/maps/layers'); } catch { layers = { unavailable: {} }; } } build(); if (ensureMap()) setTimeout(() => { map.invalidateSize(); }, 50); },
    update() {}, focus(place) { if (place?.boundingbox && ensureMap()) { map.fitBounds([[place.boundingbox[0], place.boundingbox[2]], [place.boundingbox[1], place.boundingbox[3]]]); L.marker([place.lat, place.lon]).addTo(groups.search).bindPopup(esc(place.name)); } } };
}
