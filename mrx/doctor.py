"""`python -m mrx --doctor` — prints what M.R.X. can see (never secret values) and makes one real AI request."""
from __future__ import annotations

import asyncio
import importlib.metadata as md
import os
import platform
import sys
import time
from pathlib import Path


def _ver(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return "NOT INSTALLED"


def run() -> int:
    from . import __version__
    from .agent.llm import LLMError
    from .core.config import data_dir
    from .runtime import Runtime

    root = Path(__file__).resolve().parents[1]
    print(f"M.R.X. {__version__}   folder: {root}")
    print(f"Python {platform.python_version()} on {platform.platform()}")
    for pkg in ("anthropic", "fastapi", "uvicorn", "playwright", "keyring", "psutil", "pyautogui"):
        print(f"  {pkg:<11} {_ver(pkg)}")
    rt = Runtime()
    print(f"\nData folder: {data_dir()}")
    print(f"Secret storage: {rt.secrets.backend}")
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "YOUTUBE_API_KEY"):
        where = rt.secrets.locate(name)
        v = rt.secrets.get(name) or ""
        shown = f"{v[:7]}…{v[-4:]} ({len(v)} chars)" if v else "-"
        print(f"  {name}: {'FOUND in ' + ', '.join(where) if where else 'NOT FOUND'}  {shown}")
    for f in (root / ".env", data_dir() / ".env"):
        print(f"  .env file {f}: {'exists' if f.exists() else 'not present'}")
    print(f"Model: {rt.settings.get('ai.model')}")
    ok, why = rt.agent.provider.available()
    print(f"AI provider preference: {rt.settings.get('ai.provider')}  (Gemini model: {rt.settings.get('ai.gemini_model')}, Claude model: {rt.settings.get('ai.model')})")
    if not ok:
        print(f"\nAI engine: NOT ACTIVE - {why}")
        print("Fix: run setup_key.bat (Windows) / ./setup_key.sh, then start M.R.X. again.")
        return 1

    async def ping(p) -> str:
        async def _n(_t: str) -> None: ...
        t0 = time.perf_counter()
        turn = await asyncio.wait_for(p.stream_turn("Reply with the single word: ok", [{"role": "user", "content": "ping"}], [], _n), 75)
        return f"OK - replied {turn.text.strip()!r} in {int((time.perf_counter() - t0) * 1000)} ms"
    provs = getattr(rt.agent.provider, "providers", {"default": rt.agent.provider})
    good = 0
    for n, p in provs.items():
        if not p.available()[0]:
            continue
        try:
            print(f"\nLive test [{p.describe()}]:", asyncio.run(ping(p)))
            good += 1
        except LLMError as e:
            print(f"\nLive test [{p.describe()}]: FAILED - {e}")
        except Exception as e:  # noqa: BLE001
            print(f"\nLive test [{p.describe()}]: FAILED - {type(e).__name__}: {e}")
    return 0 if good else 1
