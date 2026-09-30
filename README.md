# M.R.X. — real-time autonomous desktop AI agent

M.R.X. is a local-first agent that operates your computer, browser, files, connected services and local network from
natural-language commands (typed or spoken), and reports **what it actually did and verified** — not what it hopes happened.

```
LISTEN → UNDERSTAND → PLAN → EXECUTE → OBSERVE → VERIFY → RESPOND
```

## Quick start

**Windows** — put the folder on your Desktop, then:

```bat
python scripts\place_on_desktop.py        :: copies the project to Desktop\M.R.X   (or just clone this repo there)
cd %USERPROFILE%\Desktop\M.R.X
install.bat                                :: venv + dependencies + automation browser
run.bat                                    :: starts M.R.X and opens http://127.0.0.1:8765
```

macOS / Linux: `./install.sh` then `./run.sh`. Python 3.10+ is required.

> The project was built in a cloud session, which cannot write to your PC's Desktop. `scripts/place_on_desktop.py`
> (or `git clone` into your Desktop) puts it there; it understands OneDrive-redirected Desktops.

### Updating

Double-click **`update.bat`** (or `python scripts/update.py`). If your folder is a `git clone` you can also just run
`git pull origin claude/nice-einstein-nhlhj1` and then `update.bat` (it installs any new dependencies). Only if your folder is
an old copy that has no `scripts/update.py` at all, download that one file first — never do this in a git clone (git will refuse to
pull over it; delete the file and `git pull` instead). It downloads the latest version from GitHub (or runs `git pull`
if you cloned), keeps your `.env`, settings and memories, then tells you to restart. **`doctor.bat`** prints diagnostics
(never secrets) and tests the AI connection. The version is shown in the app header.

### Free AI with Google Gemini

M.R.X. supports **Google Gemini** as well as Claude. Get a free key at https://aistudio.google.com/apikey, then either run
`setup_key.bat` and choose **1**, or click **Enable AI engine…** on the dashboard. The free tier has request-per-minute and
per-day limits (M.R.X. tells you when one is hit); default model `gemini-2.5-flash`, changeable in Settings → AI. If both keys are
set, the preferred provider (Settings → AI) is used first and the other is a fallback — e.g. Claude out of credits → Gemini.
The Gemini path is tested against a mocked stream and Google's real request validation, but **not yet with a live key**.

### Setting your API key (any one of these)

0. **Simplest:** double-click **`setup_key.bat`**, paste the key, press Enter. It saves `.env` in the M.R.X. folder and tests it.

1. **In the app (easiest, no restart):** dashboard → **Enable AI engine…** (or Settings → Security). Stored in the OS keychain
   (Windows Credential Manager); if none exists it offers a private `~/.mrx/.env` file. Then **Test AI connection** shows the exact problem if any.
2. **`.env` file:** copy `.env.example` to `.env` in the project folder, fill in `ANTHROPIC_API_KEY=...`, restart `run.bat`.
3. **Environment variable:** `set ANTHROPIC_API_KEY=...` (Windows CMD) **in the same window, before** `run.bat`.
   (`set` prints nothing, and a running M.R.X. never sees a variable set afterwards or in another window.)

> **"Your credit balance is too low"?** The key works but the Anthropic *API* account has no credits. Add credits at
> https://console.anthropic.com → Plans & Billing. (A Claude.ai Pro/Max subscription does not include API credits.)

### Optional keys

| Key | Enables |
|---|---|
| `GEMINI_API_KEY` (free) or `ANTHROPIC_API_KEY` | The AI engine: open-ended requests, multi-step planning, summarising, writing, recovery by changing strategy |
| `YOUTUBE_API_KEY` | YouTube search (playing a pasted URL/ID works without it) |
| `MRX_EMAIL_*` | Email over IMAP/SMTP (app password) |
| `MRX_MASTODON_*` | Mastodon provider |

Without `ANTHROPIC_API_KEY` M.R.X. still runs commands with a **deterministic offline command engine** (open/close apps,
create/move/copy/rename/delete files, web/YouTube search, network scan, news, memory…). Anything it can't map, it says so.

## What is real, and what is not

Nothing here is simulated. Each capability either works against the real system/API, or reports that it is unavailable.

| Area | Status |
|---|---|
| Agent loop, tool registry (86 tools), task manager (pause/resume/cancel/retry), event bus, WebSocket/SSE streaming | Implemented, tested |
| Verification (file exists, process running, page URL/title, form value read back, file downloaded, player state, SMTP accepted…) | Implemented; tools return `verified: true / false / null`. `null` = sent but not observable — M.R.X. says "sent", never "done" |
| Files & folders (incl. PDF/DOCX/XLSX reading, zip/tar, trash-based delete) | Implemented, tested |
| Windows control: apps, processes, windows, keyboard, mouse, screenshot, OCR, clipboard, power | Implemented. Input/window/OCR tools need `pyautogui` / `pygetwindow` / Tesseract and a desktop session; otherwise they report *unavailable*. Only developed on Linux — **Windows behaviour is untested** |
| Browser (Playwright): tabs, navigate, search, click with recovery ladder, forms, JS, downloads/uploads, dialogs, attach via CDP | Implemented, tested against real Chromium |
| Built-in YouTube player (IFrame API), playback verified from the player's own reported state | Implemented; search needs `YOUTUBE_API_KEY`. Some videos disallow embedding — reported, not hidden |
| Memory (long-term, preference, episodic, semantic, project) | Implemented. Retrieval is a **local hashed n-gram index**, lexical-similarity not neural embeddings. Secrets are refused |
| Email (IMAP/SMTP; draft → review → send; separate permission) | Implemented, tested with a stubbed server only — **not run against a real mailbox** |
| Social (provider interface + Mastodon) | Implemented, untested against a live instance |
| Live news (real RSS/Atom, source/time/link, freshness label) | Implemented. Claim provenance (confirmed/official/reported…) is **not** auto-assigned; articles are labelled "not independently verified" |
| World map (Leaflet + OpenStreetMap; USGS earthquakes, OpenSky flights, Open-Meteo weather, Nominatim search) | Implemented. Each layer shows provider + data time; failures show "unavailable". Traffic and geolocated-news layers are **not available** (no keyless legitimate source) |
| Network radar (ARP/neighbour, ICMP on your own subnet, SSDP, mDNS, MAC vendor lookup, radar visual) | Implemented, tested; refuses non-private ranges, never port-scans. Wired/Wi-Fi is not observable → "Unknown". Vendor names: small built-in table + IEEE registry via *Update vendor database* |
| Voice: wake word, push-to-talk, continuous, streaming recognition, sentence-streamed TTS, barge-in, multilingual | Implemented in the browser (Web Speech API; Chrome/Edge). Uses the system default mic/speaker. While M.R.X. speaks, only "stop"/the wake word interrupts (to avoid hearing itself). Logic unit-tested; **real microphone use is untested here** |
| Calendar | **Not implemented** |
| Gmail/Outlook OAuth flows | **Not implemented** (IMAP/SMTP app-password provider only; the provider interface is ready) |
| AI engine (Claude and Gemini, streaming tool use, automatic fallback) | Implemented; exercised in tests with scripted/mocked models. Claude's request path was confirmed to reach the live API (billing error seen); **neither has completed a full task with a live key yet** |

## Safety model

* **Confirmation levels** — 1 automatic · 2 confirm · 3 always confirm. Defaults: reads/opening known apps automatic;
  move/rename/delete file, close apps, kill process, run JS, upload, keyboard/mouse input, unknown executables → confirm;
  delete folder, shells/interpreters, power actions, sending email → always confirm. Change any of them, or mark level-2 tools
  *trusted*, in Settings → Automation. Turning off *auto-execution* makes every state-changing action ask.
* **Draft → review → send** for email; auto-send is off unless you enable it.
* Deleted files go to `~/.mrx/trash` (recoverable).
* The file tools are confined to your home folder (+ allowed roots) and can never read or write credential stores
  (`.ssh`, `.aws`…), shell start-up files, auto-start folders, browser profiles or M.R.X.'s own private data.
* Text from web pages, emails, files and news is treated as **untrusted data** by the agent's instructions; it cannot authorise actions.
* Loopback-only server, per-launch token on every API/WebSocket call, Host and Origin checks (blocks DNS-rebinding and CSRF).
* Secrets live in the OS keychain/env, are redacted from logs, audit records and events, and never enter LLM prompts.
* Every tool call is audited (Debug → tool audit log).

## Architecture

```
mrx/
  core/      config · event bus · sqlite db · redaction & secret store
  tools/     registry (validation, confirmation, retries, audit, events) · filesystem · system · browser
  services/  memory · network radar · news · maps · youtube · email · social
  agent/     orchestrator · task manager · LLM provider · offline planner · context (it/that/second one) · language detection
  api/       FastAPI: REST + /ws/events + SSE
  plugins.py plugin system (drop a .py with PLUGIN = Plugin(...) into ~/.mrx/plugins/)
ui/          dependency-free web UI (dashboard, tasks, browser, YouTube, files, news, map, network, memory, settings, debug)
tests/       pytest (backend, real Chromium) + node:test (UI state & voice logic)
```

Data lives in `~/.mrx` (`MRX_HOME` overrides): `settings.json`, `mrx.db`, `trash/`, `screenshots/`, `browser-profile/`, `plugins/`.

## Development

```bash
pip install -r requirements-dev.txt && playwright install chromium   # dev = core + optional + pytest
python -m pytest            # 134 tests; browser tests skip if Chromium cannot start
npm test                    # UI state/voice logic (node ≥ 20, no dependencies)
```

Voice commands to try: "Hey M.R.X., open Chrome" · "Create a folder called Project X on my desktop" ·
"Scan my network" · "Show today's global news" · "Remember that my project folder is on D drive" · "Stop".
