"""Plugin system. A plugin bundles tools + metadata; new capabilities can be dropped into <data dir>/plugins/*.py
without touching the core. A plugin file defines `PLUGIN = Plugin(...)`. Plugin files are ordinary Python that runs
with your privileges: only install plugins you trust."""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .tools.registry import Tool

log = logging.getLogger("mrx.plugins")


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    permissions: list[str]
    tools: Callable[[], list[Tool]]
    events: list[str] = field(default_factory=list)      # event types the plugin emits
    configuration: dict = field(default_factory=dict)    # default config
    builtin: bool = True


def builtin_plugins() -> list[Plugin]:
    from .tools import filesystem, system, browser
    from .services import memory, network, news, maps, youtube, email_service, social
    return [
        Plugin("filesystem", "1.0", "Create, read, modify, copy, move, rename, delete, search and archive files.", ["fs.read", "fs.write", "fs.delete"], filesystem.tools, ["file.created", "file.modified"]),
        Plugin("windows", "1.0", "Applications, processes, windows, keyboard, mouse, screen, clipboard, telemetry.", ["process.launch", "process.kill", "input.control", "screen.capture"], system.tools, ["system.alert"]),
        Plugin("browser", "1.0", "Playwright browser automation with verification.", ["browser.control", "net.access"], browser.tools, ["browser.navigation", "browser.download", "browser.dialog"]),
        Plugin("memory", "1.0", "Persistent local memory with retrieval.", ["memory.read", "memory.write"], memory.tools),
        Plugin("network", "1.0", "Local network radar (ARP/ICMP/SSDP/mDNS).", ["net.local_discovery"], network.tools, ["network.device_found", "network.scan_completed"]),
        Plugin("news", "1.0", "Live news from real RSS/Atom feeds.", ["net.access"], news.tools),
        Plugin("maps", "1.0", "Live map layers (USGS, Open-Meteo, OpenSky, Nominatim).", ["net.access"], maps.tools),
        Plugin("youtube", "1.0", "Built-in YouTube player and search.", ["net.access", "ui.player"], youtube.tools, ["youtube.load", "youtube.state"]),
        Plugin("email", "1.0", "IMAP/SMTP email with draft-then-send workflow.", ["email.read", "email.draft", "email.send"], email_service.tools, ["email.received", "email.sent"]),
        Plugin("social", "1.0", "Provider-based social integrations (Mastodon).", ["social.read", "social.publish"], social.tools),
    ]


def load_external(dirpath: Path) -> list[Plugin]:
    out = []
    if not dirpath.is_dir():
        return out
    for f in sorted(dirpath.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"mrx_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            p = getattr(mod, "PLUGIN", None)
            if isinstance(p, Plugin):
                p.builtin = False
                out.append(p)
            else:
                log.warning("plugin %s defines no PLUGIN", f.name)
        except Exception as e:  # a broken plugin must not stop M.R.X. from starting
            log.warning("plugin %s failed to load: %s", f.name, e)
    return out
