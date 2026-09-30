"""LLM provider layer. The orchestrator only depends on `LLMProvider.stream_turn`, so providers are swappable
and tests can inject a scripted provider."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("mrx.llm")


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    text: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw_content: Any = None            # provider-native assistant content, replayed unchanged
    usage: dict = field(default_factory=dict)


class LLMError(Exception):
    """User-presentable failure (auth, rate limit, offline...)."""


def api_message(e: Exception) -> str:
    """The provider's own error text, without the SDK's repr noise."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        m = (body.get("error") or {}).get("message") or body.get("message")
        if m:
            return str(m)
    return str(getattr(e, "message", None) or e)


def friendly_api_error(msg: str) -> str | None:
    low = msg.lower()
    if "credit balance is too low" in low or "billing" in low and "credit" in low:
        return ("Your Anthropic API account has no credits left. Add credits at https://console.anthropic.com "
                "→ Plans & Billing, then try again. (API credits are separate from a Claude.ai subscription.)")
    if "invalid x-api-key" in low or "invalid api key" in low:
        return "The Anthropic API key was rejected. Check that it is correct and active."
    if "overloaded" in low:
        return "The AI provider is overloaded right now. Try again in a moment."
    return None


_PARAM_HINTS = ("extra inputs", "not permitted", "output_config", "effort", "unexpected", "unknown", "fallbacks", "beta",
                "thinking", "not supported", "unrecognized")


def is_param_problem(msg: str) -> bool:
    """A 400 that is about an optional request parameter (worth retrying without it) — not billing, model or content."""
    low = msg.lower()
    return friendly_api_error(msg) is None and any(h in low for h in _PARAM_HINTS)


class LLMProvider(ABC):
    name = "abstract"

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...

    @abstractmethod
    async def stream_turn(self, system: str, messages: list, tools: list[dict],
                          on_text: Callable[[str], Awaitable[None]]) -> Turn: ...

    @abstractmethod
    def tool_result_message(self, results: list[tuple[str, str, bool]]) -> dict: ...


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, rt):
        self.rt = rt
        self._client = None
        self._key = None

    def _api_key(self) -> str | None:
        return self.rt.secrets.get("ANTHROPIC_API_KEY") or self.rt.secrets.get("ANTHROPIC_AUTH_TOKEN")

    def available(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "the 'anthropic' package is not installed"
        if not self._api_key():
            return False, "no ANTHROPIC_API_KEY configured (Settings → Security, or environment variable)"
        return True, ""

    def client(self):
        import anthropic
        key = self._api_key()
        if self._client is None or key != self._key:
            # Bounded: a stalled connection must surface as an error, never as a silent hang.
            # A plain number works across SDK versions (newer ones use a different httpx package).
            self._client, self._key = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=90.0), key
        return self._client

    def tool_result_message(self, results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": content, **({"is_error": True} if err else {})}
            for tid, content, err in results]}

    async def stream_turn(self, system, messages, tools, on_text):
        import anthropic
        cfg = self.rt.settings.get("ai", {})
        params: dict[str, Any] = dict(
            model=cfg.get("model", "claude-opus-5-5"), max_tokens=int(cfg.get("max_tokens", 16000)),
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}], messages=messages)
        if tools:
            params["tools"] = tools
        with_effort = dict(params, output_config={"effort": cfg["effort"]}) if cfg.get("effort") else None
        # Ladder of attempts, most featureful first. An SDK/model that rejects an optional parameter simply
        # moves down a rung; only the last rung's error is shown to the user.
        rungs: list[tuple[bool, dict]] = []
        if cfg.get("refusal_fallback", False):
            rungs.append((True, with_effort or params))
        rungs.append((False, with_effort or params))
        if with_effort:
            rungs.append((False, params))
        last: Exception | None = None
        try:
            c = self.client()
            for beta, prm in rungs:
                try:
                    return await self._run(c.beta.messages.stream if beta else c.messages.stream, prm, on_text, beta=beta)
                except TypeError as e:
                    last = e
                    log.warning("AI request variant not supported by this SDK (%s): %s", "beta" if beta else "plain", e)
                except anthropic.BadRequestError as e:
                    if not is_param_problem(api_message(e)):
                        raise                      # billing / model / content problems: retrying only repeats the charge
                    last = e
                    log.warning("AI request variant rejected (%s): %s", "beta" if beta else "plain", api_message(e))
            if isinstance(last, anthropic.BadRequestError):
                raise last
            raise LLMError(f"The installed anthropic SDK does not support this request ({last}). Run: pip install -U anthropic")
        except anthropic.BadRequestError as e:
            msg = api_message(e)
            raise LLMError(friendly_api_error(msg) or f"The AI provider rejected the request: {msg[:300]}")
        except anthropic.AuthenticationError:
            raise LLMError("The Anthropic API key was rejected. Check that it is correct and active.")
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"The API key is not allowed to use model '{params['model']}': {api_message(e)[:200]}")
        except anthropic.NotFoundError:
            raise LLMError(f"Model '{params['model']}' was not found for this API key. Change it in Settings → AI.")
        except anthropic.RateLimitError:
            raise LLMError("The AI provider is rate limiting requests. Try again in a moment.")
        except anthropic.APITimeoutError:
            raise LLMError("The AI provider did not answer in time (90 s). Check your connection/firewall.")
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Cannot reach the AI provider (offline, firewall or proxy?): {type(e.__cause__ or e).__name__}")
        except anthropic.APIStatusError as e:
            msg = api_message(e)
            raise LLMError(friendly_api_error(msg) or f"AI provider error {e.status_code}: {msg[:200]}")

    async def _run(self, stream_fn, params, on_text, beta: bool) -> Turn:
        kw = dict(params)
        if beta:
            kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        async with stream_fn(**kw) as stream:
            async for text in stream.text_stream:
                await on_text(text)
            final = await stream.get_final_message()
        text = "".join(b.text for b in final.content if b.type == "text")
        uses = [ToolUse(b.id, b.name, dict(b.input)) for b in final.content if b.type == "tool_use"]
        u = final.usage
        return Turn(text, uses, final.stop_reason or "end_turn", final.content,
                    {"in": u.input_tokens, "out": u.output_tokens,
                     "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0})
