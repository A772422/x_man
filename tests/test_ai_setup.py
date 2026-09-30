import types

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedProvider, wait_task
from mrx.agent.llm import AnthropicProvider, LLMError, LLMProvider, Turn
from mrx.api.app import create_app
from mrx.runtime import Runtime


async def say(rt, text):
    r = await rt.agent.submit(text, "t")
    return await wait_task(rt, r["task_id"])


async def test_greetings_get_a_reply_without_ai(rt):
    for g in ("hello", "hey", "Hi", "hey mrx", "Hello there!", "namaste", "नमस्ते", "good morning"):
        t = await say(rt, g)
        assert t.result.startswith("Hello! I'm M.R.X."), (g, t.result)
        assert "AI engine is not active" in t.result and "Settings → Security" in t.result   # says how to fix it
    t = await say(rt, "what can you do")
    assert "operates your computer" in t.result


async def test_greeting_when_ai_key_is_bad_shows_the_real_reason(rt):
    class Bad(LLMProvider):
        def available(self): return True, ""
        async def stream_turn(self, *a, **k): raise LLMError("The Anthropic API key was rejected. Check that it is correct and active.")
        def tool_result_message(self, r): return {}
    rt.agent.provider = Bad()
    t = await say(rt, "hello")
    assert "API key was rejected" in t.result and "Test AI connection" in t.result
    t = await say(rt, "write me a poem")
    assert "API key was rejected" in t.result and "add an ANTHROPIC_API_KEY" not in t.result


async def test_llm_answers_conversation(rt):
    rt.agent.provider = ScriptedProvider([Turn("Hello! How can I help?", [], "end_turn")])
    t = await say(rt, "hello")
    assert t.result == "Hello! How can I help?" and t.outcome == "COMPLETED"


# ---- the SDK request ladder: an unsupported optional parameter must not break chat --------------------------------
class FakeStream:
    def __init__(self, text): self.text = text
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    @property
    def text_stream(self):
        async def g():
            yield self.text
        return g()
    async def get_final_message(self):
        blk = types.SimpleNamespace(type="text", text=self.text)
        return types.SimpleNamespace(content=[blk], stop_reason="end_turn", usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))


def provider_with(rt, stream_fn):
    p = AnthropicProvider(rt)
    fake = types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream_fn), beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream_fn)))
    p.client = lambda: fake
    return p


async def test_request_ladder_drops_unsupported_effort(rt):
    seen = []

    def stream(**kw):
        seen.append(sorted(kw))
        if "output_config" in kw:
            raise TypeError("unexpected keyword argument 'output_config'")
        return FakeStream("hi there")
    got = []
    async def on_text(t): got.append(t)
    turn = await provider_with(rt, stream).stream_turn("sys", [{"role": "user", "content": "hello"}], [], on_text)
    assert turn.text == "hi there" and got == ["hi there"]
    assert "output_config" in seen[0] and "output_config" not in seen[-1]
    assert "tools" not in seen[-1]   # empty tool list is omitted, not sent


async def test_final_rung_error_is_shown_to_user(rt):
    def stream(**kw): raise TypeError("nope")
    with pytest.raises(LLMError) as e:
        await provider_with(rt, stream).stream_turn("s", [{"role": "user", "content": "x"}], [], lambda t: None)
    assert "pip install -U anthropic" in str(e.value)


# ---- .env support and the connection-test endpoint -----------------------------------------------------------------
def test_env_file_loader(tmp_path, monkeypatch):
    from mrx.__main__ import load_env_files
    f = tmp_path / ".env"
    f.write_text('# comment\nANTHROPIC_API_KEY="sk-ant-from-file-123"\nexport YOUTUBE_API_KEY=yt-key\nEMPTY=\nALREADY=new\n')
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False); monkeypatch.delenv("YOUTUBE_API_KEY", raising=False); monkeypatch.setenv("ALREADY", "keep")
    loaded = load_env_files([f, tmp_path / "missing.env"])
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-file-123" and os.environ["YOUTUBE_API_KEY"] == "yt-key"
    assert os.environ["ALREADY"] == "keep" and "EMPTY" not in loaded   # existing variables win


def test_ai_test_endpoint(home):
    rt = Runtime(home / ".mrx")
    with TestClient(create_app(rt, "t")) as c:
        H = {"x-mrx-token": "t"}
        r = c.post("/api/ai/test", headers=H).json()
        assert r["ok"] is False and "ANTHROPIC_API_KEY" in r["error"]          # no key → says so
        rt.agent.provider = ScriptedProvider([Turn("ok", [], "end_turn")])
        r = c.post("/api/ai/test", headers=H).json()
        assert r["ok"] is True and r["reply"] == "ok"

        class Bad(LLMProvider):
            def available(self): return True, ""
            async def stream_turn(self, *a, **k): raise LLMError("Model 'x' was not found for this API key.")
            def tool_result_message(self, r): return {}
        rt.agent.provider = Bad()
        r = c.post("/api/ai/test", headers=H).json()
        assert r["ok"] is False and "not found" in r["error"]


def test_key_saved_to_private_file_when_no_keychain(home, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rt = Runtime(home / ".mrx")
    rt.secrets._kr = None      # simulate a machine without an OS keychain
    with TestClient(create_app(rt, "t")) as c:
        H = {"x-mrx-token": "t"}
        r = c.post("/api/secrets/ANTHROPIC_API_KEY", headers=H, json={"value": "sk-ant-abc123456789"})
        assert r.status_code == 409 and r.json()["needs_file_fallback"] is True
        assert rt.agent.engine()[0] == "local"
        r = c.post("/api/secrets/ANTHROPIC_API_KEY", headers=H, json={"value": "sk-ant-abc123456789", "allow_file": True})
        assert r.json()["stored"] == "file"
        assert rt.agent.engine()[0] == "llm"                               # applied immediately, no restart
        assert "sk-ant-abc" in (home / ".mrx/.env").read_text()
        listed = c.get("/api/secrets", headers=H).json()["secrets"]["ANTHROPIC_API_KEY"]
        assert listed == {"set": True, "source": "file"} and "sk-ant" not in str(c.get("/api/secrets", headers=H).json())
        assert "sk-ant" not in rt.settings.path.read_text() if rt.settings.path.exists() else True
        c.delete("/api/secrets/ANTHROPIC_API_KEY", headers=H)
        assert rt.agent.engine()[0] == "local" and "sk-ant" not in (home / ".mrx/.env").read_text()


def test_real_sdk_client_can_be_constructed(rt, monkeypatch):
    """Guards against SDK-version mismatches (e.g. timeout types) that only appear with the real client."""
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    p = AnthropicProvider(rt)
    assert p.available() == (True, "")
    c = p.client()
    assert c.__class__.__name__ == "AsyncAnthropic" and p.client() is c   # cached
