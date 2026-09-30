"""Runtime container: wires settings, database, event bus, tools, memory, browser, radar and the agent."""
from __future__ import annotations

from pathlib import Path

import httpx

from .agent.llm import LLMProvider
from .agent.orchestrator import Agent
from .core.config import Settings
from .core.db import Database
from .core.events import EventBus
from .core.security import SecretStore
from .plugins import Plugin, builtin_plugins, load_external
from .services.email_service import ImapSmtpProvider
from .services.memory import MemoryStore
from .services.network import NetworkRadar
from .tools.browser import BrowserController
from .tools.registry import ToolRegistry

SERVICE_OF_TOOL = {"get_live_news": "news", "get_world_map_data": "maps", "show_map": "maps",
                   "play_youtube": "youtube", "youtube_search": "youtube", "scan_network": "network"}


class Runtime:
    def __init__(self, home: Path | None = None, provider: LLMProvider | None = None):
        self.settings = Settings(home)
        self.db = Database(self.settings.db_path)
        self.bus = EventBus()
        self.secrets = SecretStore(self.settings.home)
        self.http = httpx.AsyncClient(headers={"User-Agent": "M.R.X./0.1"}, follow_redirects=True)
        self.registry = ToolRegistry(self)
        self.memory = MemoryStore(self.db)
        self.browser = BrowserController(self)
        self.radar = NetworkRadar(self)
        self.email = ImapSmtpProvider(self.secrets)
        self.youtube_state: dict = {}
        self.plugins: dict[str, Plugin] = {}
        self.health: dict[str, dict] = {}
        self._ui = 0
        self.agent = Agent(self, provider)
        self.bus.subscribe("youtube.state", self._on_youtube_state)
        self.bus.subscribe("tool.*", self._track_health)
        self.load_plugins()

    # ---- UI presence -------------------------------------------------------------------------------
    def ui_clients(self) -> int:
        return self._ui

    def ui_attached(self, delta: int) -> None:
        self._ui = max(0, self._ui + delta)

    def _on_youtube_state(self, ev: dict) -> None:
        self.youtube_state = {**self.youtube_state, **ev["data"], "ts": ev["ts"]}

    def _track_health(self, ev: dict) -> None:
        svc = SERVICE_OF_TOOL.get(ev["data"].get("tool", ""))
        if not svc:
            return
        if ev["type"] == "tool.completed":
            self.health[svc] = {"state": "CONNECTED", "last_ok": ev["ts"], "error": None}
        else:
            prev = self.health.get(svc, {})
            self.health[svc] = {"state": "OFFLINE" if prev.get("state") != "CONNECTED" else "DEGRADED",
                                "last_ok": prev.get("last_ok"), "error": ev["data"].get("error")}

    # ---- plugins -----------------------------------------------------------------------------------
    def load_plugins(self) -> None:
        disabled = set(self.settings.get("plugins.disabled", []) or [])
        self.registry.tools.clear()
        self.plugins.clear()
        for p in builtin_plugins() + load_external(self.settings.home / "plugins"):
            self.plugins[p.name] = p
            if p.name in disabled:
                continue
            for t in p.tools():
                t.plugin = p.name
                self.registry.register(t)

    def set_plugin_enabled(self, name: str, enabled: bool) -> None:
        if name not in self.plugins:
            raise KeyError(name)
        disabled = set(self.settings.get("plugins.disabled", []) or [])
        (disabled.discard if enabled else disabled.add)(name)
        self.settings.update({"plugins": {"disabled": sorted(disabled)}})
        self.load_plugins()

    # ---- status ------------------------------------------------------------------------------------
    def status(self) -> dict:
        engine, why = self.agent.engine()
        email_ok, email_why = self.email.configured()
        yt = self.secrets.has("YOUTUBE_API_KEY")
        services = {
            "ai": {"state": "CONNECTED" if engine == "llm" else "DEGRADED",
                   "detail": "AI engine online" if engine == "llm" else f"offline command engine only — {why}"},
            "filesystem": {"state": "LOCAL", "detail": "local"},
            "windows": {"state": "LOCAL", "detail": "local"},
            "memory": {"state": "LOCAL" if self.settings.get("memory.enabled", True) else "OFFLINE", "detail": "local database"},
            "browser": {"state": "LOCAL", "detail": "running" if self.browser.running else "not started"},
            "network": {"state": "LOCAL", "detail": "local network radar"},
            "email": {"state": "CONNECTED" if email_ok else "OFFLINE", "detail": "configured" if email_ok else email_why},
            "youtube": {"state": "CONNECTED" if yt else "DEGRADED",
                        "detail": "search enabled" if yt else "no YOUTUBE_API_KEY: play by URL/ID only"},
        }
        for svc in ("news", "maps"):
            h = self.health.get(svc)
            services[svc] = {"state": h["state"] if h else "UNKNOWN",
                             "detail": (h.get("error") if h else None) or ("live data provider" if h else "not queried yet")}
        for svc in ("youtube",):
            h = self.health.get(svc)
            if h and h["state"] in ("OFFLINE", "DEGRADED") and h.get("error") and "not configured" not in (h["error"] or ""):
                services[svc] = {"state": h["state"], "detail": h["error"]}
        from . import __version__
        return {"version": __version__, "engine": engine, "services": services, "secrets_backend": self.secrets.backend,
                "model": self.settings.get("ai.model"), "ui_clients": self._ui}

    async def close(self) -> None:
        await self.browser.close()
        await self.http.aclose()
        self.db.close()
