import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrx.agent.llm import LLMProvider, ToolUse, Turn  # noqa: E402
from mrx.runtime import Runtime  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Desktop").mkdir(parents=True)
    (h / "Documents").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("MRX_HOME", str(h / ".mrx"))
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "YOUTUBE_API_KEY", "MRX_EMAIL_ADDRESS", "MRX_EMAIL_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    return h


class ScriptedProvider(LLMProvider):
    """Test double for the LLM: replays scripted turns so the orchestrator loop can be tested without a network."""
    name = "scripted"

    def __init__(self, turns):
        self.turns, self.calls = list(turns), []

    def available(self):
        return True, ""

    async def stream_turn(self, system, messages, tools, on_text):
        self.calls.append(messages[-1])
        t = self.turns.pop(0)
        if t.text:
            await on_text(t.text)
        t.raw_content = [{"type": "text", "text": t.text}] + [{"type": "tool_use", "id": u.id, "name": u.name, "input": u.input} for u in t.tool_uses]
        return t

    def tool_result_message(self, results):
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": c, "is_error": e} for i, c, e in results]}


@pytest.fixture
async def rt(home):
    r = Runtime(home / ".mrx")
    yield r
    await r.close()


async def wait_task(rt, tid, timeout=10):
    t = rt.agent.tasks.tasks[tid]
    end = asyncio.get_event_loop().time() + timeout
    while t.status not in ("COMPLETED", "FAILED", "CANCELLED"):
        if asyncio.get_event_loop().time() > end:
            raise AssertionError(f"task stuck in {t.status}: {t.current_action}")
        await asyncio.sleep(0.02)
    return t


def auto_confirm(rt, approve=True):
    """Answer every confirmation prompt immediately (stands in for the user clicking Confirm/Cancel)."""
    rt.bus.subscribe("confirm.required", lambda ev: rt.registry.confirmations.resolve(ev["data"]["id"], approve))
