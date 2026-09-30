"""Settings: JSON file in the data dir, secrets only from env / OS keychain."""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

APP_NAME = "M.R.X."

DEFAULTS: dict[str, Any] = {
    "ai": {
        "provider": "auto",            # auto | gemini | anthropic (the other one is used as a fallback)
        "model": "claude-opus-5-5",     # Anthropic model
        "gemini_model": "gemini-2.5-flash",
        "effort": "medium",
        "max_tokens": 16000,
        "streaming": True,
        "max_steps": 12,
        "refusal_fallback": False,
    },
    "voice": {
        "wake_word": "hey mrx",
        "language": "auto",
        "speech_rate": 1.0,
        "mode": "push_to_talk",  # push_to_talk | wake_word | continuous
        "voice_name": "",
        "speak_replies": True,
    },
    "automation": {
        "retry_limit": 2,
        "parallel_execution": True,
        "auto_execute": True,
        "confirmation_timeout_s": 120,
        # tool name -> level (1 automatic, 2 confirm, 3 always confirm)
        "confirmation_overrides": {},
        # level-2 tools the user has marked as trusted (run without asking)
        "trusted_tools": [],
    },
    "filesystem": {
        # Extra roots the file tools may touch, besides the user's home directory.
        "allowed_roots": [],
        "trash_instead_of_delete": True,
    },
    "browser": {
        "engine": "chromium",
        "channel": "",  # e.g. "chrome" to use installed Google Chrome
        "headless": False,
        "persistent_profile": True,
        "download_dir": "",
        "cdp_url": "",  # attach to an existing browser started with --remote-debugging-port
    },
    "memory": {"enabled": True},
    "email": {"auto_send": False},
    "network": {"scan_interval_s": 0, "methods": ["arp", "ping", "ssdp", "mdns"], "device_names": {}},
    "news": {
        "feeds": {
            "World": [
                ["BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"],
                ["Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"],
                ["NPR", "https://feeds.npr.org/1004/rss.xml"],
            ],
            "India": [
                ["The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"],
                ["BBC India", "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml"],
            ],
            "Technology": [
                ["BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"],
                ["Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"],
            ],
            "Science": [["BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"]],
            "Business": [["BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"]],
            "Sports": [["BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"]],
            "Entertainment": [["BBC Entertainment", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"]],
            "Security": [["Krebs on Security", "https://krebsonsecurity.com/feed/"]],
        }
    },
}


def data_dir() -> Path:
    p = Path(os.environ.get("MRX_HOME") or (Path.home() / ".mrx"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("confirmation_overrides", "device_names", "feeds"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Thread-safe settings store persisted to <data_dir>/settings.json."""

    def __init__(self, home: Path | None = None):
        self.home = home or data_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "settings.json"
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                self._data = _merge(DEFAULTS, json.loads(self.path.read_text("utf-8")))
            except (OSError, ValueError):
                pass  # corrupt file: fall back to defaults, do not crash

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            cur: Any = self._data
            for part in dotted.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return copy.deepcopy(cur)

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._data = _merge(self._data, patch)
            self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
            return copy.deepcopy(self._data)

    @property
    def db_path(self) -> Path:
        return self.home / "mrx.db"

    @property
    def downloads_dir(self) -> Path:
        d = self.get("browser.download_dir") or str(Path.home() / "Downloads")
        p = Path(d)
        p.mkdir(parents=True, exist_ok=True)
        return p
