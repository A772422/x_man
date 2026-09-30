"""Dynamic tool registry. Every capability is a structured tool that returns a ToolResult.
The registry owns permissions, confirmation, retries, timing, audit logging and events."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from ..core.security import redact


# Tools that only observe (no side effects). Used to decide which calls "auto_execute=false" still lets through.
READ_ONLY = {"read_file", "list_directory", "search_files", "file_info", "get_system_stats", "list_processes", "scan_network",
             "get_live_news", "get_world_map_data", "recall_memory", "list_memories", "read_page", "extract_links", "list_tabs",
             "is_application_running", "identify_device", "read_email", "list_emails", "search_email", "youtube_search",
             "youtube_status", "social_list_providers", "social_read", "social_search", "mouse_position", "clipboard_get",
             "list_windows", "extract_structured", "screen_ocr", "show_map", "screenshot", "browser_screenshot"}


class ToolError(Exception):
    """Raised by a tool handler. `transient=True` means a bounded retry may help."""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class Outcome:
    """Return this from a handler to report the verification state explicitly."""
    result: Any = None
    verified: bool | None = None  # None = nothing to verify (pure read), True/False = checked
    note: str = ""


@dataclass
class ToolResult:
    success: bool
    tool: str
    result: Any = None
    error: str | None = None
    execution_time_ms: int = 0
    verified: bool | None = None
    verification: str = ""
    status: str = "completed"  # completed | failed | declined | unavailable

    def to_dict(self) -> dict:
        return asdict(self)

    def to_llm(self, limit: int = 12000) -> str:
        s = json.dumps(redact(self.to_dict()), ensure_ascii=False, default=str)
        return s if len(s) <= limit else s[:limit] + "…[truncated]"


@dataclass
class Tool:
    name: str
    description: str
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    handler: Callable[..., Any] | None = None
    risk: int = 1                      # 1 automatic, 2 confirm, 3 always confirm
    risk_fn: Callable[[dict, "Runtime"], int] | None = None
    plugin: str = "core"
    scope: str = ""
    retryable: bool = False
    timeout_s: float = 120.0
    available: Callable[["Runtime"], tuple[bool, str]] | None = None
    parallel_safe: bool = True

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": self.properties,
                                 "required": self.required, "additionalProperties": False}}


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate_args(tool: Tool, args: dict) -> str | None:
    for r in tool.required:
        if r not in args or args[r] is None:
            return f"missing required argument '{r}'"
    for k, v in args.items():
        spec = tool.properties.get(k)
        if spec is None:
            return f"unknown argument '{k}'"
        t = spec.get("type")
        if t and v is not None and t in _TYPES:
            if t == "integer" and isinstance(v, bool):
                return f"argument '{k}' must be integer"
            if not isinstance(v, _TYPES[t]):
                return f"argument '{k}' must be {t}"
        if "enum" in spec and v not in spec["enum"]:
            return f"argument '{k}' must be one of {spec['enum']}"
    return None


class ConfirmationManager:
    def __init__(self, runtime: "Runtime"):
        self.rt = runtime
        self.pending: dict[str, dict] = {}
        self._futures: dict[str, asyncio.Future] = {}

    async def request(self, tool: str, args: dict, level: int, task_id: str | None, summary: str) -> bool:
        cid = uuid.uuid4().hex[:10]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._futures[cid] = fut
        info = {"id": cid, "tool": tool, "level": level, "task_id": task_id, "summary": summary,
                "arguments": redact(args), "created_at": time.time()}
        self.pending[cid] = info
        await self.rt.bus.emit("confirm.required", info)
        try:
            return await asyncio.wait_for(fut, self.rt.settings.get("automation.confirmation_timeout_s", 120))
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(cid, None)
            self._futures.pop(cid, None)
            await self.rt.bus.emit("confirm.resolved", {"id": cid, "tool": tool})

    def resolve(self, cid: str, approved: bool) -> bool:
        fut = self._futures.get(cid)
        if fut and not fut.done():
            fut.set_result(approved)
            return True
        return False


class ToolRegistry:
    def __init__(self, runtime: "Runtime"):
        self.rt = runtime
        self.tools: dict[str, Tool] = {}
        self.confirmations = ConfirmationManager(runtime)

    def register(self, tool: Tool) -> Tool:
        self.tools[tool.name] = tool
        return tool

    def unregister_plugin(self, plugin: str) -> None:
        for n in [n for n, t in self.tools.items() if t.plugin == plugin]:
            del self.tools[n]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def describe(self) -> list[dict]:
        out = []
        for t in self.tools.values():
            ok, why = (True, "")
            if t.available:
                try:
                    ok, why = t.available(self.rt)
                except Exception as e:  # availability probe must never crash listing
                    ok, why = False, str(e)
            out.append({"name": t.name, "description": t.description, "plugin": t.plugin, "risk": t.risk,
                        "scope": t.scope, "available": ok, "unavailable_reason": why,
                        "level": self.effective_level(t, {}), "schema": t.schema()["input_schema"]})
        return sorted(out, key=lambda x: (x["plugin"], x["name"]))

    def llm_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def effective_level(self, tool: Tool, args: dict) -> int:
        ov = self.rt.settings.get("automation.confirmation_overrides", {}) or {}
        if tool.name in ov:
            return int(ov[tool.name])
        level = tool.risk_fn(args, self.rt) if tool.risk_fn else tool.risk
        if level == 2 and tool.name in (self.rt.settings.get("automation.trusted_tools", []) or []):
            return 1
        if level < 2 and tool.name not in READ_ONLY and not self.rt.settings.get("automation.auto_execute", True):
            return 2  # auto-execution off: every state-changing action asks first
        return level

    async def execute(self, name: str, args: dict | None = None, task_id: str | None = None) -> ToolResult:
        args = dict(args or {})
        t0 = time.perf_counter()
        tool = self.tools.get(name)

        def finish(res: ToolResult) -> ToolResult:
            res.execution_time_ms = int((time.perf_counter() - t0) * 1000)
            return res

        if tool is None:
            return finish(ToolResult(False, name, error=f"unknown tool '{name}'", status="failed"))
        bad = validate_args(tool, args)
        if bad:
            return finish(ToolResult(False, name, error=f"invalid arguments: {bad}", status="failed"))
        if tool.available:
            try:
                ok, why = tool.available(self.rt)
            except Exception as e:
                ok, why = False, str(e)
            if not ok:
                res = finish(ToolResult(False, name, error=f"unavailable: {why}", status="unavailable"))
                await self._record(task_id, tool, args, res)
                return res

        level = self.effective_level(tool, args)
        if level >= 2:
            approved = await self.confirmations.request(name, args, level, task_id,
                                                        f"{tool.description.splitlines()[0]}")
            if not approved:
                res = finish(ToolResult(False, name, error="declined by user (or confirmation timed out)",
                                        status="declined"))
                await self._record(task_id, tool, args, res)
                return res

        await self.rt.bus.emit("tool.started", {"tool": name, "arguments": redact(args)}, task_id=task_id)
        attempts = 1 + (max(0, int(self.rt.settings.get("automation.retry_limit", 2))) if tool.retryable else 0)
        last_err = "unknown error"
        res: ToolResult | None = None
        for attempt in range(1, attempts + 1):
            try:
                if tool.handler is None:
                    raise ToolError("tool has no handler")
                if inspect.iscoroutinefunction(tool.handler):
                    out = await asyncio.wait_for(tool.handler(self.rt, **args), tool.timeout_s)
                else:  # sync handlers run in a worker thread so the UI/event loop never blocks
                    out = await asyncio.wait_for(asyncio.to_thread(tool.handler, self.rt, **args), tool.timeout_s)
                if isinstance(out, Outcome):
                    ok = out.verified is not False
                    res = ToolResult(ok, name, out.result, None if ok else f"verification failed: {out.note}",
                                     verified=out.verified, verification=out.note,
                                     status="completed" if ok else "failed")
                else:
                    res = ToolResult(True, name, out)
                break
            except ToolError as e:
                last_err = str(e)
                if e.transient and attempt < attempts:
                    await asyncio.sleep(min(0.4 * attempt, 2))
                    continue
                res = ToolResult(False, name, error=last_err, status="failed")
                break
            except asyncio.TimeoutError:
                res = ToolResult(False, name, error=f"timed out after {tool.timeout_s:.0f}s", status="failed")
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - tools must never crash the agent
                res = ToolResult(False, name, error=f"{type(e).__name__}: {e}", status="failed")
                break
        assert res is not None
        res = finish(res)
        await self._record(task_id, tool, args, res)
        return res

    async def _record(self, task_id: str | None, tool: Tool, args: dict, res: ToolResult) -> None:
        red_args = redact(args)
        summary = json.dumps(redact(res.result), ensure_ascii=False, default=str)[:1500]
        self.rt.db.execute(
            "INSERT INTO tool_calls(ts,task_id,tool,arguments_redacted,status,duration_ms,result,error,verified) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (time.time(), task_id, tool.name, json.dumps(red_args, default=str), res.status,
             res.execution_time_ms, summary, res.error, None if res.verified is None else int(res.verified)))
        etype = "tool.completed" if res.success else "tool.failed"
        await self.rt.bus.emit(etype, {"tool": tool.name, "arguments": red_args, "success": res.success,
                                       "error": res.error, "verified": res.verified,
                                       "verification": res.verification, "ms": res.execution_time_ms,
                                       "status": res.status, "read_only": tool.name in READ_ONLY,
                                       "result_preview": summary[:400]}, task_id=task_id)
