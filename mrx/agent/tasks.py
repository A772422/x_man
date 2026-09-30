"""Task manager: long-running, non-blocking tasks with pause / resume / cancel / retry and live progress."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

STATUSES = ["QUEUED", "PLANNING", "RUNNING", "WAITING", "VERIFYING", "COMPLETED", "FAILED", "CANCELLED"]
FINAL = {"COMPLETED", "FAILED", "CANCELLED"}
# Execution-state vocabulary shown in the console (task status + verification outcome)
OUTCOMES = ["REQUESTED", "PLANNING", "EXECUTING", "VERIFYING", "COMPLETED", "FAILED", "PARTIALLY_COMPLETED"]


@dataclass
class Task:
    command: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "QUEUED"
    outcome: str = "REQUESTED"
    progress: float = 0.0
    current_action: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    conversation_id: str = ""
    steps: list[dict] = field(default_factory=list)
    _pause: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _runner: asyncio.Task | None = field(default=None, repr=False)

    def __post_init__(self):
        self._pause.set()

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "command", "status", "outcome", "progress", "current_action",
                                              "result", "error", "created_at", "started_at", "completed_at",
                                              "conversation_id", "steps")}


class TaskManager:
    def __init__(self, rt):
        self.rt = rt
        self.tasks: dict[str, Task] = {}

    def create(self, command: str, conversation_id: str = "") -> Task:
        t = Task(command=command, conversation_id=conversation_id)
        self.tasks[t.id] = t
        self.rt.db.execute("INSERT INTO tasks(id,command,status,outcome,progress,created_at,conversation_id) VALUES(?,?,?,?,?,?,?)",
                           (t.id, command, t.status, t.outcome, 0, t.created_at, conversation_id))
        return t

    async def set(self, t: Task, status: str | None = None, outcome: str | None = None, progress: float | None = None,
                  action: str | None = None, result: Any = None, error: str | None = None) -> None:
        if t.status in FINAL and status not in (None, t.status):
            return  # a finished task never comes back to life (retry creates a new one)
        if status:
            t.status = status
            if status == "RUNNING" and t.started_at is None:
                t.started_at = time.time()
            if status in FINAL:
                t.completed_at = time.time()
        if outcome:
            t.outcome = outcome
        if progress is not None:
            t.progress = max(0.0, min(1.0, progress))
        if action is not None:
            t.current_action = action
        if result is not None:
            t.result = result
        if error is not None:
            t.error = error
        self.rt.db.execute("UPDATE tasks SET status=?,outcome=?,progress=?,current_action=?,result=?,error=?,started_at=?,completed_at=? WHERE id=?",
                           (t.status, t.outcome, t.progress, t.current_action, str(t.result)[:4000] if t.result is not None else None,
                            t.error, t.started_at, t.completed_at, t.id))
        await self.rt.bus.emit("task.updated", t.public(), task_id=t.id)
        if status == "COMPLETED":
            await self.rt.bus.emit("task.completed", t.public(), task_id=t.id)

    async def step(self, t: Task, title: str, status: str = "running", detail: str = "", idx: int | None = None) -> int:
        if idx is None:
            t.steps.append({"idx": len(t.steps), "title": title, "status": status, "detail": detail})
            idx = len(t.steps) - 1
            self.rt.db.execute("INSERT INTO task_steps(task_id,idx,title,status,detail,created_at) VALUES(?,?,?,?,?,?)",
                               (t.id, idx, title, status, detail, time.time()))
        else:
            t.steps[idx].update(status=status, detail=detail or t.steps[idx]["detail"], title=title or t.steps[idx]["title"])
            self.rt.db.execute("UPDATE task_steps SET status=?,detail=?,title=? WHERE task_id=? AND idx=?",
                               (status, t.steps[idx]["detail"], t.steps[idx]["title"], t.id, idx))
        await self.rt.bus.emit("task.step", {"task_id": t.id, "step": t.steps[idx]}, task_id=t.id)
        return idx

    async def checkpoint(self, t: Task) -> None:
        """Cooperative pause point: called between steps."""
        if not t._pause.is_set():
            await self.set(t, status="WAITING", action="paused")
            await t._pause.wait()
            await self.set(t, status="RUNNING")

    def pause(self, tid: str) -> bool:
        t = self.tasks.get(tid)
        if t and t.status not in FINAL:
            t._pause.clear()
            return True
        return False

    def resume(self, tid: str) -> bool:
        t = self.tasks.get(tid)
        if t and t.status not in FINAL:
            t._pause.set()
            return True
        return False

    async def cancel(self, tid: str) -> bool:
        t = self.tasks.get(tid)
        if not t or t.status in FINAL:
            return False
        t._pause.set()
        if t._runner and not t._runner.done():
            t._runner.cancel()
        await self.set(t, status="CANCELLED", outcome="FAILED", action="cancelled by user")
        return True

    def active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status not in FINAL]

    def latest(self, final_ok: bool = True) -> Task | None:
        ts = sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)
        return ts[0] if ts and (final_ok or ts[0].status not in FINAL) else None

    def list(self, limit: int = 50) -> list[dict]:
        return [t.public() for t in sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]]
