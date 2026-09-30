"""Google Gemini provider (plain REST over httpx — no extra dependency).

Uses streamGenerateContent (SSE) with function calling. Tool schemas are converted to Gemini's OpenAPI subset
(no additionalProperties, no empty OBJECT types). The model's own response parts — including any `thoughtSignature`
— are replayed unchanged on the next turn, which Gemini requires for multi-step tool use."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

from .llm import LLMError, LLMProvider, ToolUse, Turn

log = logging.getLogger("mrx.gemini")
BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_TYPES = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT"}


def clean_schema(s: dict) -> dict:
    """JSON Schema (as used for Claude tools) → Gemini Schema."""
    t, desc = s.get("type", "string"), s.get("description", "")
    if t == "object":
        props = {k: clean_schema(v) for k, v in (s.get("properties") or {}).items()}
        if not props:  # Gemini rejects an OBJECT with no properties
            out: dict[str, Any] = {"type": "STRING"}
            desc = (desc + " " if desc else "") + "(a JSON object, passed as a string)"
        else:
            out = {"type": "OBJECT", "properties": props}
            req = [r for r in s.get("required", []) if r in props]
            if req:
                out["required"] = req
    elif t == "array":
        out = {"type": "ARRAY", "items": clean_schema(s.get("items") or {"type": "string"})}
    else:
        out = {"type": _TYPES.get(t, "STRING")}
        if "enum" in s:
            out.update(type="STRING", enum=[str(x) for x in s["enum"]])
    if desc:
        out["description"] = desc
    return out


def function_declarations(tools: list[dict]) -> list[dict]:
    decls = []
    for t in tools:
        d: dict[str, Any] = {"name": t["name"], "description": t["description"][:1000]}
        sch = t.get("input_schema") or {}
        if sch.get("properties"):
            d["parameters"] = clean_schema(sch)
        decls.append(d)
    return [{"functionDeclarations": decls}]


def to_contents(messages: list[dict]) -> list[dict]:
    """Internal (Claude-style) message list → Gemini `contents`. Lists are already Gemini parts."""
    out = []
    for m in messages:
        c = m["content"]
        out.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": c}] if isinstance(c, str) else c})
    return out


def _merge_text(parts: list[dict]) -> list[dict]:
    """Streaming splits text into chunks; join neighbours that carry no signature, keep everything else verbatim."""
    out: list[dict] = []
    for p in parts:
        if out and "text" in p and "text" in out[-1] and not p.get("thought") and not out[-1].get("thought") \
                and set(p) <= {"text"} and set(out[-1]) <= {"text"}:
            out[-1] = {"text": out[-1]["text"] + p["text"]}
        else:
            out.append(dict(p))
    return out


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, rt):
        self.rt = rt

    def _key(self) -> str | None:
        for n in KEY_NAMES:
            v = self.rt.secrets.get(n)
            if v:
                return v
        return None

    def model(self) -> str:
        return self.rt.settings.get("ai.gemini_model", DEFAULT_MODEL) or DEFAULT_MODEL

    def describe(self) -> str:
        return f"Gemini · {self.model()}"

    def available(self):
        return (True, "") if self._key() else (False, "no GEMINI_API_KEY configured (free key: https://aistudio.google.com/apikey)")

    def tool_result_message(self, results):
        parts = []
        for tid, content, is_err in results:
            name = tid.rsplit("::", 1)[0]
            try:
                val = json.loads(content)
            except ValueError:
                val = content
            parts.append({"functionResponse": {"name": name, "response": {"error": val} if is_err else {"output": val}}})
        return {"role": "user", "content": parts}   # all results in ONE turn, as Gemini requires

    async def list_models(self) -> list[str]:
        key = self._key()
        if not key:
            return []
        r = await self.rt.http.get(f"{BASE}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=20)
        if r.status_code != 200:
            return []
        return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]

    async def diagnose(self) -> list[str]:
        """One tiny request per endpoint variant; returns human-readable lines (status + Google's message, never the key)."""
        key, model = self._key(), self.model()
        if not key:
            return ["no key"]
        body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}], "generationConfig": {"maxOutputTokens": 8}}
        m = quote(model, safe="")
        lines = []
        for label, url in (("v1beta generateContent", f"{BASE}/models/{m}:generateContent"),
                           ("v1 generateContent", f"https://generativelanguage.googleapis.com/v1/models/{m}:generateContent"),
                           ("vertex-express generateContent", f"https://aiplatform.googleapis.com/v1/publishers/google/models/{m}:generateContent")):
            try:
                r = await self.rt.http.post(url, json=body, headers={"x-goog-api-key": key}, timeout=30)
                try:
                    msg = r.json().get("error", {}).get("message", "OK" if r.status_code == 200 else r.text[:120])
                except ValueError:
                    msg = r.text[:120]
                lines.append(f"{label}: HTTP {r.status_code} - {msg[:160]}")
            except httpx.HTTPError as e:
                lines.append(f"{label}: {type(e).__name__}")
        return lines

    async def _error(self, status: int, raw: str, model: str) -> LLMError:
        try:
            j = json.loads(raw).get("error", {})
        except ValueError:
            j = {}
        msg, st = j.get("message") or raw[:200], j.get("status", "")
        low = (msg + raw).lower()
        if "api key not valid" in low or "api_key_invalid" in low or "api key expired" in low:
            return LLMError("The Gemini API key was rejected. Create a free key at https://aistudio.google.com/apikey and enter it again.")
        if status == 429 or st == "RESOURCE_EXHAUSTED":
            m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', raw)
            wait = f" Try again in about {int(float(m.group(1))) + 1} seconds." if m else " Try again in a minute."
            zero = " This model appears to have no free-tier quota — pick a Flash model in Settings → AI." if "limit: 0" in low else ""
            return LLMError("Gemini's free-tier rate limit was reached (requests per minute/day are capped)." + wait + zero)
        if status == 404:
            names = [n for n in await self.list_models() if "gemini" in n][:10]
            listed = model in names
            hint = (f" Google lists it for your key, so this is an endpoint/key-type problem rather than a wrong name." if listed
                    else f" Available: {', '.join(names)}. Change it in Settings → AI." if names else " Change it in Settings → AI.")
            return LLMError(f"Gemini returned 404 for model '{model}'. Google said: {msg[:220]}.{hint}")
        if "location is not supported" in low:
            return LLMError("Gemini is not available in your region for this key/project.")
        if status in (401, 403):
            return LLMError(f"Gemini refused the request ({st or status}): {msg[:200]}")
        if status in (500, 503):
            return LLMError("Gemini is temporarily overloaded or unavailable. Try again shortly.")
        return LLMError(f"Gemini error {status}: {msg[:300]}")

    async def stream_turn(self, system, messages, tools, on_text: Callable[[str], Awaitable[None]]) -> Turn:
        cfg = self.rt.settings.get("ai", {})
        model, key = self.model(), self._key()
        if not key:
            raise LLMError("No Gemini API key configured.")
        body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": system}]}, "contents": to_contents(messages),
                                "generationConfig": {"maxOutputTokens": min(int(cfg.get("max_tokens", 16000)), 32768)}}
        if tools:
            body["tools"] = function_declarations(tools)
        headers = {"x-goog-api-key": key, "content-type": "application/json"}
        # Ladder: streaming on v1beta → plain request on v1beta → plain request on the stable v1 API. Only a 404 moves down
        # a rung (some key types/projects are only routable on some endpoints); every other error is final.
        first_404: str | None = None
        for api, streaming in (("v1beta", True), ("v1beta", False), ("v1", False), ("vertex", True), ("vertex", False)):
            try:
                return await self._request(api, streaming, model, headers, body, on_text)
            except _NotFound as nf:
                first_404 = first_404 or nf.raw
                log.warning("Gemini 404 on %s %s", api, "stream" if streaming else "generate")
            except LLMError:
                if api != "vertex" or first_404 is None:
                    raise               # the Vertex address is a last resort: its errors must not hide the original 404
        raise await self._error(404, first_404 or "", model)

    async def _request(self, api: str, streaming: bool, model: str, headers: dict, body: dict, on_text) -> Turn:
        method = "streamGenerateContent?alt=sse" if streaming else "generateContent"
        m = quote(model, safe="")
        url = (f"https://aiplatform.googleapis.com/v1/publishers/google/models/{m}:{method}" if api == "vertex"
               else f"https://generativelanguage.googleapis.com/{api}/models/{m}:{method}")
        for attempt in range(3):
            try:
                async with self.rt.http.stream("POST", url, json=body, headers=headers, timeout=httpx.Timeout(90.0)) as r:
                    if r.status_code == 200:
                        if streaming:
                            return await self._assemble(_sse_events(r), on_text)
                        return await self._assemble(_one(json.loads((await r.aread()).decode("utf-8", "replace"))), on_text)
                    raw = (await r.aread()).decode("utf-8", "replace")
                    status = r.status_code
                if status == 404:
                    raise _NotFound(raw)
                err = await self._error(status, raw, model)
                if status in (500, 503) and attempt < 2:
                    log.warning("Gemini %s, retrying", status)
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise err
            except httpx.TimeoutException:
                raise LLMError("Gemini did not answer in time (90 s). Check your connection.")
            except httpx.HTTPError as e:
                raise LLMError(f"Cannot reach Gemini (offline, firewall or proxy?): {type(e).__name__}")
        raise LLMError("Gemini is temporarily unavailable.")

    async def _assemble(self, events, on_text) -> Turn:
        parts: list[dict] = []
        finish, block, usage = None, None, {}
        async for ev in events:
            if "error" in ev:
                raise await self._error(int(ev["error"].get("code", 500)), json.dumps(ev), self.model())
            block = (ev.get("promptFeedback") or {}).get("blockReason") or block
            usage = ev.get("usageMetadata") or usage
            for cand in ev.get("candidates", []):
                finish = cand.get("finishReason") or finish
                for p in (cand.get("content") or {}).get("parts", []):
                    parts.append(p)
                    if p.get("text") and not p.get("thought"):
                        await on_text(p["text"])
        raw = _merge_text(parts)
        text = "".join(p["text"] for p in raw if p.get("text") and not p.get("thought"))
        uses = [ToolUse(f"{p['functionCall']['name']}::{i}", p["functionCall"]["name"], dict(p["functionCall"].get("args") or {}))
                for i, p in enumerate(x for x in raw if "functionCall" in x)]
        u = {"in": usage.get("promptTokenCount", 0), "out": usage.get("candidatesTokenCount", 0), "cache_read": usage.get("cachedContentTokenCount", 0)}
        if block or finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            return Turn(text, [], "refusal", raw, u)
        if uses:
            return Turn(text, uses, "tool_use", raw, u)
        if not text:
            raise LLMError(f"Gemini returned an empty response (finishReason={finish}). Try rephrasing, or pick another model in Settings → AI.")
        return Turn(text, [], "max_tokens" if finish == "MAX_TOKENS" else "end_turn", raw, u)


class _NotFound(Exception):
    def __init__(self, raw: str):
        super().__init__(raw)
        self.raw = raw


async def _one(event: dict):
    yield event


async def _sse_events(r):
    async for line in r.aiter_lines():
        if line.startswith("data:"):
            try:
                yield json.loads(line[5:].strip())
            except ValueError:
                continue
