import asyncio
import json

from mrx.tools.registry import Outcome, Tool, ToolError


async def test_unknown_tool_and_bad_args(rt):
    r = await rt.registry.execute("nope", {})
    assert not r.success and "unknown tool" in r.error
    r = await rt.registry.execute("create_folder", {})
    assert not r.success and "missing required" in r.error
    r = await rt.registry.execute("create_folder", {"path": 5})
    assert not r.success and "must be string" in r.error


async def test_structured_result_shape(rt, home):
    r = await rt.registry.execute("create_folder", {"path": "Desktop/A"})
    d = r.to_dict()
    assert set(d) >= {"success", "tool", "result", "error", "execution_time_ms", "verified"}
    assert d["success"] and d["verified"] is True and d["execution_time_ms"] >= 0


async def test_verification_failure_is_not_success(rt):
    rt.registry.register(Tool("liar", "claims success", {}, [], lambda rt_: Outcome({"x": 1}, False, "file is missing")))
    r = await rt.registry.execute("liar", {})
    assert r.success is False and r.status == "failed" and "verification failed" in r.error


async def test_confirmation_decline_and_approve(rt, home):
    (home / "a.txt").write_text("x")
    task = asyncio.create_task(rt.registry.execute("delete_file", {"path": "a.txt"}))
    ev = await rt.bus.wait_for("confirm.required", timeout=3)
    assert ev and ev["data"]["level"] == 2
    rt.registry.confirmations.resolve(ev["data"]["id"], False)
    r = await task
    assert r.status == "declined" and (home / "a.txt").exists()

    task = asyncio.create_task(rt.registry.execute("delete_file", {"path": "a.txt"}))
    ev = await rt.bus.wait_for("confirm.required", timeout=3)
    rt.registry.confirmations.resolve(ev["data"]["id"], True)
    r = await task
    assert r.success and r.verified and not (home / "a.txt").exists()


async def test_trusted_tool_skips_level2_but_not_level3(rt, home):
    rt.settings.update({"automation": {"trusted_tools": ["delete_file", "delete_folder"]}})
    (home / "b.txt").write_text("x")
    r = await asyncio.wait_for(rt.registry.execute("delete_file", {"path": "b.txt"}), 3)
    assert r.success  # level 2 + trusted → no prompt
    (home / "d").mkdir()
    t = asyncio.create_task(rt.registry.execute("delete_folder", {"path": "d"}))
    ev = await rt.bus.wait_for("confirm.required", timeout=3)
    assert ev is not None  # level 3 always asks
    rt.registry.confirmations.resolve(ev["data"]["id"], False)
    await t


async def test_confirmation_timeout_declines(rt, home):
    rt.settings.update({"automation": {"confirmation_timeout_s": 0.1}})
    (home / "c.txt").write_text("x")
    r = await rt.registry.execute("delete_file", {"path": "c.txt"})
    assert r.status == "declined" and (home / "c.txt").exists()


async def test_bounded_retry_only_for_transient(rt):
    calls = {"n": 0}

    def flaky(rt_):
        calls["n"] += 1
        raise ToolError("net blip", transient=True)
    rt.registry.register(Tool("flaky", "d", {}, [], flaky, retryable=True))
    r = await rt.registry.execute("flaky", {})
    assert not r.success and calls["n"] == 1 + rt.settings.get("automation.retry_limit")  # bounded, not infinite

    calls["n"] = 0

    def perm(rt_):
        calls["n"] += 1
        raise ToolError("nope")
    rt.registry.register(Tool("perm", "d", {}, [], perm, retryable=True))
    await rt.registry.execute("perm", {})
    assert calls["n"] == 1


async def test_audit_log_redacts_secrets(rt):
    rt.registry.register(Tool("secretive", "d", {"api_key": {"type": "string"}, "note": {"type": "string"}}, [], lambda rt_, api_key, note: {"ok": 1}))
    await rt.registry.execute("secretive", {"api_key": "sk-ant-abcdefghijklmnop123", "note": "token sk-ant-zzzzzzzzzzzzzzzz9999"})
    row = rt.db.one("SELECT arguments_redacted FROM tool_calls WHERE tool='secretive'")
    assert "sk-ant" not in row["arguments_redacted"] and "REDACTED" in row["arguments_redacted"]


async def test_sync_tools_do_not_block_event_loop(rt):
    import time
    rt.registry.register(Tool("slow", "d", {}, [], lambda rt_: time.sleep(0.4) or "done"))
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1
    t = asyncio.create_task(ticker())
    await rt.registry.execute("slow", {})
    t.cancel()
    assert ticks >= 10


async def test_events_emitted(rt, home):
    q = rt.bus.open_queue()
    await rt.registry.execute("create_folder", {"path": "E"})
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert types == ["tool.started", "tool.completed"]
