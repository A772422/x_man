import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import wait_task
from mrx.agent import gemini as g
from mrx.agent.llm import LLMError, LLMProvider, Router, Turn
from mrx.api.app import create_app
from mrx.runtime import Runtime


async def nop(_t): ...


def sse(*events):
    return b"".join(b"data: " + json.dumps(e).encode() + b"\r\n\r\n" for e in events)


def cand(*parts, finish=None):
    c = {"content": {"role": "model", "parts": list(parts)}}
    if finish:
        c["finishReason"] = finish
    return {"candidates": [c], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}


class Fake:
    """Scripted Gemini endpoint; records every request body/headers."""
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models") and request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                                                        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]}]})
        r = self.responses.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, content=r, headers={"content-type": "text/event-stream"})

    def body(self, i):
        return json.loads(self.requests[i].content)


@pytest.fixture
def gem(rt, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-key-000000000000000000")
    def install(*responses):
        f = Fake(*responses)
        rt.http = httpx.AsyncClient(transport=httpx.MockTransport(f))
        return f
    return install


# ---- schema conversion ---------------------------------------------------------------------------------------------
def test_tool_schemas_are_converted_to_gemini_format(rt):
    decls = g.function_declarations(rt.registry.llm_schemas())[0]["functionDeclarations"]
    assert len(decls) == len(rt.registry.tools)
    blob = json.dumps(decls)
    assert "additionalProperties" not in blob and '"input_schema"' not in blob

    def walk(s):
        if s.get("type") == "OBJECT":
            assert s.get("properties"), s                     # Gemini rejects OBJECT without properties
        for v in (s.get("properties") or {}).values():
            walk(v)
        if "items" in s:
            walk(s["items"])
        assert s.get("type") in {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY", "OBJECT"}
    for d in decls:
        if "parameters" in d:
            walk(d["parameters"])
    by = {d["name"]: d for d in decls}
    assert "parameters" not in by["list_tabs"]                 # no-argument tools omit parameters
    assert by["fill_form"]["parameters"]["properties"]["fields"]["items"]["type"] == "OBJECT"
    assert by["scroll_page"]["parameters"]["properties"]["direction"] == {"type": "STRING", "enum": ["up", "down"]}


# ---- streaming ---------------------------------------------------------------------------------------------------------
async def test_streams_text_and_key_goes_in_header_not_url(rt, gem):
    f = gem(sse(cand({"text": "Hel"}), cand({"text": "lo!"}, finish="STOP")))
    got = []
    async def on_text(t): got.append(t)
    turn = await g.GeminiProvider(rt).stream_turn("SYS", [{"role": "user", "content": "hi"}], [], on_text)
    assert got == ["Hel", "lo!"] and turn.text == "Hello!" and turn.stop_reason == "end_turn"
    req = f.requests[0]
    assert "AIza" not in str(req.url) and req.headers["x-goog-api-key"].startswith("AIza")
    assert f.body(0)["systemInstruction"]["parts"][0]["text"] == "SYS" and "tools" not in f.body(0)
    assert "streamGenerateContent" in str(req.url) and "alt=sse" in str(req.url)


# ---- full agent loop through Gemini ---------------------------------------------------------------------------------
async def run_task(rt, text):
    r = await rt.agent.submit(text, "g")
    return await wait_task(rt, r["task_id"])


async def test_tool_loop_executes_replays_signature_and_verifies(rt, gem, home):
    call = {"functionCall": {"name": "create_folder", "args": {"path": "Desktop/GeminiDir"}}, "thoughtSignature": "SIG123"}
    f = gem(sse(cand({"text": "On it. "}, call, finish="STOP")), sse(cand({"text": "Created the folder."}, finish="STOP")))
    rt.settings.update({"ai": {"provider": "gemini"}})
    t = await run_task(rt, "make a folder GeminiDir on the desktop")
    assert (home / "Desktop/GeminiDir").is_dir() and t.outcome == "COMPLETED" and t.result == "Created the folder."
    second = f.body(1)["contents"]
    model_turn, result_turn = second[-2], second[-1]
    assert model_turn["role"] == "model" and any(p.get("thoughtSignature") == "SIG123" for p in model_turn["parts"])   # replayed verbatim
    fr = result_turn["parts"][0]["functionResponse"]
    assert result_turn["role"] == "user" and fr["name"] == "create_folder" and fr["response"]["output"]["success"] is True
    assert fr["response"]["output"]["verified"] is True
    assert "tools" in f.body(0) and len(f.body(0)["tools"][0]["functionDeclarations"]) == len(rt.registry.tools)


async def test_parallel_calls_answered_in_one_turn(rt, gem, home):
    calls = [{"functionCall": {"name": "create_folder", "args": {"path": f"Desktop/P{i}"}}} for i in range(2)]
    f = gem(sse(cand(*calls, finish="STOP")), sse(cand({"text": "Both done."}, finish="STOP")))
    rt.settings.update({"ai": {"provider": "gemini"}})
    await run_task(rt, "two folders")
    assert (home / "Desktop/P0").is_dir() and (home / "Desktop/P1").is_dir()
    last = f.body(1)["contents"][-1]["parts"]
    assert [p["functionResponse"]["name"] for p in last] == ["create_folder", "create_folder"]      # one turn, two responses


async def test_failed_tool_is_reported_to_model_as_error(rt, gem, home):
    f = gem(sse(cand({"functionCall": {"name": "read_file", "args": {"path": ".ssh/id_rsa"}}}, finish="STOP")), sse(cand({"text": "That file is protected."}, finish="STOP")))
    rt.settings.update({"ai": {"provider": "gemini"}})
    t = await run_task(rt, "read my ssh key")
    fr = f.body(1)["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "error" in fr and "protected" in json.dumps(fr) and t.outcome == "FAILED"


# ---- errors ----------------------------------------------------------------------------------------------------------------
async def err_for(rt, gem, status, body):
    gem(httpx.Response(status, json=body) if not isinstance(body, bytes) else httpx.Response(status, content=body))
    with pytest.raises(LLMError) as e:
        await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    return str(e.value)


async def test_error_messages(rt, gem):
    m = await err_for(rt, gem, 400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})
    assert "key was rejected" in m and "aistudio.google.com/apikey" in m
    m = await err_for(rt, gem, 429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED", "details": [{"retryDelay": "34s"}]}})
    assert "rate limit" in m and "35 seconds" in m
    m = await err_for(rt, gem, 429, {"error": {"code": 429, "message": "Quota exceeded, limit: 0", "status": "RESOURCE_EXHAUSTED"}})
    assert "no free-tier quota" in m
    nf = httpx.Response(404, json={"error": {"code": 404, "message": "models/x is not found for API version v1beta", "status": "NOT_FOUND"}})
    unauth = httpx.Response(401, json={"error": {"code": 401, "message": "invalid credentials", "status": "UNAUTHENTICATED"}})
    gem(nf, nf, nf, unauth, unauth)
    rt.settings.update({"ai": {"gemini_model": "x"}})
    with pytest.raises(LLMError) as e:
        await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    m = str(e.value)
    assert "is not found for API version" in m and "gemini-2.5-flash" in m and "embedding" not in m   # Google's own words + real model list


async def test_overload_is_retried_then_succeeds(rt, gem, monkeypatch):
    async def nosleep(_): ...
    monkeypatch.setattr(g.asyncio, "sleep", nosleep)
    f = gem(httpx.Response(503, json={"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}), sse(cand({"text": "ok"}, finish="STOP")))
    turn = await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    assert turn.text == "ok" and len(f.requests) == 2


async def test_safety_block_and_empty_response(rt, gem):
    gem(sse({"promptFeedback": {"blockReason": "SAFETY"}}))
    turn = await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    assert turn.stop_reason == "refusal"
    gem(sse(cand(finish="STOP")))
    with pytest.raises(LLMError, match="empty response"):
        await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)


# ---- router / fallback -----------------------------------------------------------------------------------------------------
class Stub(LLMProvider):
    def __init__(self, name, err=None, text="from-" ):
        self.name, self.err, self.text, self.calls = name, err, text + name, 0

    def available(self): return True, ""
    def describe(self): return self.name
    def tool_result_message(self, r): return {"role": "user", "content": []}
    async def stream_turn(self, s, m, t, on_text):
        self.calls += 1
        if self.err:
            raise LLMError(self.err)
        await on_text(self.text)
        return Turn(self.text, [], "end_turn", [{"type": "text", "text": self.text}])


async def test_falls_back_to_other_provider_and_remembers_billing_failure(rt):
    a, b = Stub("anthropic", "Your Anthropic API account has no credits left."), Stub("gemini")
    rt.agent.provider = Router(rt, {"anthropic": a, "gemini": b})
    t = await run_task(rt, "hello there friend")
    assert t.result == "from-gemini" and t.outcome == "COMPLETED" and a.calls == 1
    assert any("trying gemini" in s["title"] for s in t.steps)
    t = await run_task(rt, "hello again")
    assert a.calls == 1 and b.calls == 2               # Claude is skipped for a while instead of failing every message


async def test_preference_order_and_status(rt):
    a, b = Stub("anthropic"), Stub("gemini")
    r = Router(rt, {"anthropic": a, "gemini": b})
    assert [p.name for p in r.chain()] == ["anthropic", "gemini"]
    rt.settings.update({"ai": {"provider": "gemini"}})
    assert [p.name for p in r.chain()] == ["gemini", "anthropic"]
    rt.agent.provider = r
    st = rt.status()
    assert st["engine"] == "llm" and st["model"] == "gemini" and st["providers"] == {"anthropic": True, "gemini": True}


async def test_only_gemini_key_activates_engine(rt, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-x")
    assert rt.agent.engine()[0] == "llm" and rt.agent.provider.describe().startswith("Gemini")
    monkeypatch.delenv("GEMINI_API_KEY")
    ok, why = rt.agent.provider.available()
    assert not ok and "GEMINI_API_KEY" in why and "aistudio.google.com" in why


def test_ai_test_endpoint_reports_each_provider(home, monkeypatch):
    rt = Runtime(home / ".mrx")
    rt.agent.provider = Router(rt, {"anthropic": Stub("anthropic", "Your Anthropic API account has no credits left."), "gemini": Stub("gemini", text="ok")})
    with TestClient(create_app(rt, "t")) as c:
        r = c.post("/api/ai/test", headers={"x-mrx-token": "t"}).json()
    by = {x["provider"]: x for x in r["results"]}
    assert r["ok"] is True and by["gemini"]["ok"] and not by["anthropic"]["ok"] and "no credits" in by["anthropic"]["error"]


async def test_ai_chip_reflects_real_results_not_just_key_presence(rt, monkeypatch):
    a = Stub("gemini", "The Gemini API key was rejected.")
    rt.agent.provider = Router(rt, {"gemini": a})
    assert rt.status()["services"]["ai"]["state"] == "UNKNOWN"          # key present, never used → not claimed as connected
    await run_task(rt, "hello there")
    s = rt.status()["services"]["ai"]
    assert s["state"] == "OFFLINE" and "rejected" in s["detail"]
    rt.agent.provider = Router(rt, {"gemini": Stub("gemini")})
    await run_task(rt, "hello again")
    assert rt.status()["services"]["ai"]["state"] == "CONNECTED"


async def test_404_on_streaming_falls_back_to_plain_and_stable_endpoints(rt, gem):
    nf = httpx.Response(404, json={"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
    ok_body = httpx.Response(200, json=cand({"text": "hi from plain"}, finish="STOP"))
    f = gem(nf, ok_body)
    turn = await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    assert turn.text == "hi from plain"
    urls = [str(r.url) for r in f.requests if r.method == "POST"]
    assert "streamGenerateContent" in urls[0] and urls[1].endswith(":generateContent") and "/v1beta/" in urls[1]
    f = gem(nf, nf, ok_body)                       # third rung: stable v1 API
    turn = await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    assert turn.text == "hi from plain" and "/v1/models/" in str([str(r.url) for r in f.requests if r.method == "POST"][-1])


async def test_non_404_errors_do_not_walk_the_ladder(rt, gem):
    f = gem(httpx.Response(429, json={"error": {"code": 429, "message": "q", "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(LLMError, match="rate limit"):
        await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    assert len([r for r in f.requests if r.method == "POST"]) == 1


def test_new_style_aq_keys_accepted_by_setup_script():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import set_key
    prefix = set_key.PROVIDERS["1"][2]
    assert "AQ.Ab8RN6IKm4lQcT4B5xHeE".startswith(prefix) and "AIzaSyFake".startswith(prefix) and not "sk-ant-x".startswith(prefix)


async def test_last_resort_vertex_endpoint_used_only_after_404s(rt, gem):
    nf = httpx.Response(404, json={"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
    f = gem(nf, nf, nf, sse(cand({"text": "via vertex"}, finish="STOP")))
    turn = await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    posts = [str(r.url) for r in f.requests if r.method == "POST"]
    assert turn.text == "via vertex" and "aiplatform.googleapis.com" in posts[-1] and all("aiplatform" not in u for u in posts[:-1])


async def test_diagnose_reports_each_endpoint_without_the_key(rt, gem):
    gem(httpx.Response(404, json={"error": {"message": "not found"}}), httpx.Response(200, json=cand({"text": "x"})), httpx.Response(401, json={"error": {"message": "auth"}}))
    lines = await g.GeminiProvider(rt).diagnose()
    assert len(lines) == 3 and "HTTP 404 - not found" in lines[0] and "HTTP 200 - " in lines[1] and "HTTP 401" in lines[2]
    assert "AIza" not in " ".join(lines)


async def test_aq_key_gets_a_specific_actionable_message(rt, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.Ab8RN6IKm4lQcT4B5xHeE-fake")
    nf = httpx.Response(404, json={"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
    rt.http = httpx.AsyncClient(transport=httpx.MockTransport(Fake(nf, nf, nf, nf, nf)))
    with pytest.raises(LLMError) as e:
        await g.GeminiProvider(rt).stream_turn("s", [{"role": "user", "content": "x"}], [], nop)
    m = str(e.value)
    assert "starts with 'AQ.'" in m and "incognito" in m and "AIza" in m and "HTTP 404" in m and "AQ.Ab8RN6" not in m   # never echoes the key


async def test_all_provider_failures_are_shown_not_just_the_last(rt):
    a = Stub("anthropic", "Your Anthropic API account has no credits left.")
    b = Stub("gemini", "Google key starts with AQ. and was rejected.")
    rt.agent.provider = Router(rt, {"anthropic": a, "gemini": b})
    rt.settings.update({"ai": {"provider": "gemini"}})
    t = await run_task(rt, "hello there")
    assert "gemini: Google key starts with AQ." in t.result.lower() or "Google key starts with AQ." in t.result
    assert "no credits left" in t.result                    # both reasons visible
