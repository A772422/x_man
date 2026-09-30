import re
import sys
import types

import anthropic
import pytest

from conftest import wait_task
from mrx.agent.llm import AnthropicProvider, LLMError, api_message, friendly_api_error, is_param_problem
from mrx.tools import system as sysm

try:
    import httpx2 as hx
except ImportError:  # older SDKs
    import httpx as hx


def bad_request(msg):
    r = hx.Response(400, request=hx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.BadRequestError(msg, response=r, body={"type": "error", "error": {"type": "invalid_request_error", "message": msg}})


CREDIT = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."


def test_error_classification():
    assert "no credits" in friendly_api_error(CREDIT) and "console.anthropic.com" in friendly_api_error(CREDIT)
    assert not is_param_problem(CREDIT)                                            # billing is not a parameter problem
    assert is_param_problem("output_config.effort: Extra inputs are not permitted")
    assert api_message(bad_request(CREDIT)) == CREDIT                              # no SDK repr noise


async def test_billing_error_is_friendly_and_not_retried(rt, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    calls = []

    def stream(**kw):
        calls.append(1)
        raise bad_request(CREDIT)
    p = AnthropicProvider(rt)
    fake = types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream), beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream)))
    p.client = lambda: fake
    with pytest.raises(LLMError) as e:
        await p.stream_turn("s", [{"role": "user", "content": "hi"}], [], lambda t: None)
    assert "no credits" in str(e.value) and "{'type'" not in str(e.value)
    assert len(calls) == 1                                                        # exactly one request, not one per rung


async def test_param_rejection_still_falls_down_the_ladder(rt, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    seen = []

    class S:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        @property
        def text_stream(self):
            async def g():
                yield "ok"
            return g()
        async def get_final_message(self):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                                         usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    def stream(**kw):
        seen.append("output_config" in kw)
        if "output_config" in kw:
            raise bad_request("output_config.effort: Extra inputs are not permitted")
        return S()
    p = AnthropicProvider(rt)
    p.client = lambda: types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream))
    turn = await p.stream_turn("s", [{"role": "user", "content": "hi"}], [], lambda t: _noop())
    assert turn.text == "ok" and seen == [True, False]


async def _noop(): ...


# ---- websites vs programs ------------------------------------------------------------------------------------------
async def say(rt, text):
    r = await rt.agent.submit(text, "t")
    return await wait_task(rt, r["task_id"])


async def test_open_youtube_opens_the_website(rt, monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)
    t = await say(rt, "open YouTube")
    assert opened == ["https://www.youtube.com"] and t.outcome == "COMPLETED"
    assert "verified" not in t.result.lower() or "not observable" in t.result   # a URL hand-off cannot be verified: never claimed as verified


async def test_open_url_rejects_non_http(rt):
    r = await rt.registry.execute("open_url", {"url": "file:///etc/passwd"})
    assert not r.success and "http" in r.error


# ---- volume ---------------------------------------------------------------------------------------------------------
def test_parse_pactl():
    assert sysm.parse_pactl_volume("Volume: front-left: 29491 /  45% / -20.00 dB,   front-right: 29491 /  45% / -20.00 dB") == 45
    assert sysm.parse_pactl_volume("garbage") is None


class FakeAudio:
    """Stands in for pactl: keeps state so set → read-back verification is exercised for real."""
    def __init__(self): self.vol, self.mute = 30, False
    def run(self, cmd):
        if cmd[1] == "get-sink-volume": return f"Volume: front-left: 1 / {self.vol}% / -1 dB"
        if cmd[1] == "get-sink-mute": return f"Mute: {'yes' if self.mute else 'no'}"
        if cmd[1] == "set-sink-volume": self.vol = int(cmd[3].rstrip("%")); return ""
        if cmd[1] == "set-sink-mute": self.mute = cmd[3] == "1"; return ""
        raise AssertionError(cmd)


@pytest.fixture
def audio(monkeypatch):
    a = FakeAudio()
    monkeypatch.setattr(sysm, "WIN", False); monkeypatch.setattr(sysm, "MAC", False)
    monkeypatch.setattr(sysm.shutil, "which", lambda n: "/usr/bin/pactl" if n == "pactl" else None)
    monkeypatch.setattr(sysm, "_run", a.run)
    return a


async def test_set_volume_is_verified_by_read_back(rt, audio):
    r = await rt.registry.execute("set_volume", {"percent": 45})
    assert r.success and r.verified and r.result["percent"] == 45 and audio.vol == 45
    r = await rt.registry.execute("set_volume", {"delta": -20})
    assert audio.vol == 25
    r = await rt.registry.execute("set_volume", {"percent": 250})
    assert audio.vol == 100                                                      # clamped
    r = await rt.registry.execute("set_volume", {"mute": True})
    assert r.success and audio.mute is True
    assert (await rt.registry.execute("get_volume", {})).result == {"percent": 100, "muted": True}


async def test_volume_command_end_to_end(rt, audio):
    t = await say(rt, "set volume to 45%")
    assert t.outcome == "COMPLETED" and audio.vol == 45 and "verified" in t.result
    t = await say(rt, "mute")
    assert audio.mute is True


async def test_volume_failure_is_honest(rt, monkeypatch):
    monkeypatch.setattr(sysm, "WIN", False); monkeypatch.setattr(sysm, "MAC", False)
    monkeypatch.setattr(sysm.shutil, "which", lambda n: None)
    r = await rt.registry.execute("set_volume", {"percent": 10})
    assert not r.success and "no supported audio control" in r.error
