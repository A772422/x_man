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
            self._client, self._key = anthropic.AsyncAnthropic(api_key=key, max_retries=2), key
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
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools, messages=messages)
        if cfg.get("effort"):
            params["output_config"] = {"effort": cfg["effort"]}
        c = self.client()
        try:
            try:
                return await self._run(c.beta.messages.stream, params, on_text, beta=cfg.get("refusal_fallback", True))
            except (TypeError, anthropic.BadRequestError) as e:
                # A model/SDK that does not accept the optional beta parameters: retry once without them.
                log.info("retrying without optional beta params: %s", e)
                return await self._run(c.messages.stream, params, on_text, beta=False)
        except anthropic.AuthenticationError:
            raise LLMError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.")
        except anthropic.RateLimitError:
            raise LLMError("The AI provider is rate limiting requests. Try again in a moment.")
        except anthropic.APIConnectionError:
            raise LLMError("Cannot reach the AI provider (offline?). Local commands still work.")
        except anthropic.APIStatusError as e:
            raise LLMError(f"AI provider error {e.status_code}: {getattr(e, 'message', str(e))[:200]}")

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
