// Pure voice helpers (wake word, stop intent, language mapping, sentence streaming). Unit-tested with node.
const norm = (t) => t.toLowerCase().normalize('NFKC').replace(/[.,!?;:"'“”]/g, ' ').replace(/\s+/g, ' ').trim();

// Speech recognisers spell "M.R.X." many ways. Accept the common ones.
const WAKE_VARIANTS = ['mrx', 'm r x', 'mr x', 'm rx', 'mark x', 'mister x', 'mr ex', 'm r ex', 'mrex', 'emarex', 'em ar ex', 'एमआरएक्स', 'एम आर एक्स'];
const GREET = ['hey', 'hi', 'hello', 'ok', 'okay', 'ay', 'हे', 'हेलो', 'ਹੇ', 'ஹே'];

export function matchWakeWord(transcript, wake = 'hey mrx') {
  const t = norm(transcript);
  const w = norm(wake);
  if (!t) return null;
  const pool = new Set([w]);
  for (const g of GREET) for (const v of WAKE_VARIANTS) pool.add(`${g} ${v}`);
  let best = null;
  for (const p of pool) {
    const i = t.indexOf(p);
    if (i >= 0 && (best === null || i < best.i)) best = { i, len: p.length };
  }
  if (!best) return null;
  return { command: t.slice(best.i + best.len).trim(), matched: true };
}

const STOP_WORDS = ['stop', 'cancel', 'abort', 'halt', 'quiet', 'shut up', 'be quiet', 'ruko', 'roko', 'bas', 'chup', 'रुको', 'रोको', 'बस', 'चुप', 'ਰੁਕੋ', 'நிறுத்து', 'ఆపు', 'থামো', 'روکو', 'رکو'];
export function isStopIntent(text) {
  const t = norm(text).replace(/^(hey )?(mrx|m r x|mr x) /, '');
  return STOP_WORDS.some((w) => t === w || t.startsWith(w + ' ') || t.endsWith(' ' + w));
}

export const LANGS = [
  ['auto', 'Auto (browser language)'], ['en-IN', 'English (India)'], ['en-US', 'English (US)'], ['hi-IN', 'हिन्दी Hindi'], ['pa-IN', 'ਪੰਜਾਬੀ Punjabi'],
  ['ur-PK', 'اردو Urdu'], ['bn-IN', 'বাংলা Bengali'], ['gu-IN', 'ગુજરાતી Gujarati'], ['mr-IN', 'मराठी Marathi'], ['ta-IN', 'தமிழ் Tamil'],
  ['te-IN', 'తెలుగు Telugu'], ['kn-IN', 'ಕನ್ನಡ Kannada'], ['ml-IN', 'മലയാളം Malayalam'], ['es-ES', 'Español'], ['fr-FR', 'Français'],
  ['de-DE', 'Deutsch'], ['ar-SA', 'العربية'], ['zh-CN', '中文'], ['ja-JP', '日本語'], ['ru-RU', 'Русский'],
];

const SCRIPTS = [[/[਀-੿]/, 'pa-IN'], [/[ঀ-৿]/, 'bn-IN'], [/[઀-૿]/, 'gu-IN'], [/[஀-௿]/, 'ta-IN'], [/[ఀ-౿]/, 'te-IN'],
  [/[ಀ-೿]/, 'kn-IN'], [/[ഀ-ൿ]/, 'ml-IN'], [/[؀-ۿ]/, 'ur-PK'], [/[一-鿿]/, 'zh-CN'], [/[぀-ヿ]/, 'ja-JP'],
  [/[Ѐ-ӿ]/, 'ru-RU'], [/[ऀ-ॿ]/, 'hi-IN']];
export function speechLangFor(text, fallback = 'en-IN') {
  for (const [rx, code] of SCRIPTS) if (rx.test(text)) return code;
  return fallback;
}

export function stripForSpeech(text) {
  return text.replace(/```[\s\S]*?```/g, ' ').replace(/[`*_#>]/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' link ').replace(/^[\s✓✗!↗•\-·]+/gm, '').replace(/\s+/g, ' ').trim();
}

// Split streaming text into speakable sentences; the unfinished tail stays in `rest`.
export function takeSentences(buffer) {
  const out = []; let last = 0;
  const rx = /[^.!?।۔\n]+[.!?।۔\n]+(?=\s|$)/g; let m;
  while ((m = rx.exec(buffer))) { out.push(m[0].trim()); last = rx.lastIndex; }
  return { sentences: out.filter(Boolean), rest: buffer.slice(last) };
}
