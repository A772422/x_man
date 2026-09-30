"""Async in-process event bus. Every subscriber gets its own bounded queue so a slow
UI client can never block tool execution."""
from __future__ import annotations

import asyncio
import fnmatch
import time
import uuid
from typing import Any, Awaitable, Callable

Handler = Callable[[dict], Awaitable[None] | None]
NO_HISTORY = {"system.stats", "agent.text_delta", "voice.input", "youtube.state"}


class EventBus:
    def __init__(self, history: int = 500):
        self._handlers: list[tuple[str, Handler]] = []
        self._queues: set[asyncio.Queue] = set()
        self._history: list[dict] = []
        self._max_history = history
        self._seq = 0

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))

    def open_queue(self, maxsize: int = 1000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues.add(q)
        return q

    def close_queue(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def recent(self, limit: int = 100) -> list[dict]:
        return self._history[-limit:]

    async def emit(self, type_: str, data: dict[str, Any] | None = None, **extra: Any) -> dict:
        self._seq += 1
        event = {"id": uuid.uuid4().hex[:12], "seq": self._seq, "type": type_, "ts": time.time(),
                 "data": data or {}, **extra}
        if type_ not in NO_HISTORY:  # high-frequency events would evict everything useful
            self._history.append(event)
            if len(self._history) > self._max_history:
                del self._history[: len(self._history) - self._max_history]
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # drop for the slow consumer only
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass
        for pattern, handler in list(self._handlers):
            if fnmatch.fnmatch(type_, pattern):
                try:
                    r = handler(event)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:  # a broken subscriber must not break the emitter
                    pass
        return event

    async def wait_for(self, pattern: str, predicate: Callable[[dict], bool] | None = None,
                       timeout: float = 10.0) -> dict | None:
        q = self.open_queue()
        try:
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    return None
                try:
                    ev = await asyncio.wait_for(q.get(), left)
                except asyncio.TimeoutError:
                    return None
                if fnmatch.fnmatch(ev["type"], pattern) and (predicate is None or predicate(ev)):
                    return ev
        finally:
            self.close_queue(q)
