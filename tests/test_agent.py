import asyncio

from conftest import ScriptedProvider, auto_confirm, wait_task
from mrx.agent.llm import ToolUse, Turn
from mrx.agent.orchestrator import parse_control
from mrx.agent import planner
from mrx.agent.context import Context


async def say(rt, text, cid="t"):
    r = await rt.agent.submit(text, cid)
    return r, (await wait_task(rt, r["task_id"]) if "task_id" in r else None)


# ---- intent detection / planning (offline engine) -------------------------------------------------
def test_intent_detection():
    c = Context()
    cases = {
        "open chrome": ("open_application", {"name": "chrome"}),
        "close notepad": ("close_application", {"name": "notepad"}),
        "create a folder called Project X on my desktop": ("create_folder", {"path": "Desktop/Project X"}),
        "delete the folder Project X on desktop": ("delete_folder", {"path": "Desktop/Project X"}),
        "rename a.txt to b.txt": ("rename_file", {"path": "a.txt", "new_name": "b.txt"}),
        "search the web for latest info about rust": ("search_web", {"query": "latest info about rust"}),
        "play interstellar trailer on youtube": ("play_youtube", {"query": "interstellar trailer"}),
        "scan my network and show me connected devices": ("scan_network", {}),
        "show today's global news": ("get_live_news", {"category": "World"}),
        "remember that my project folder is on D drive": ("remember", {"content": "my project folder is on D drive"}),
        "what did I ask you to remember about my project": ("recall_memory", {"query": "my project"}),
    }
    for text, (tool, args) in cases.items():
        intents, unmapped = planner.plan(text, c)
        assert not unmapped and intents[0].tool == tool, text
        for k, v in args.items():
            assert intents[0].args[k] == v, (text, intents[0].args)


def test_multi_step_conditional_and_parallel_split():
    c = Context()
    i, _ = planner.plan("open Chrome and File Explorer", c)
    assert [x.args["name"] for x in i] == ["Chrome", "File Explorer"]
    i, _ = planner.plan("If Chrome isn't running, open it", c)
    assert i[0].kind == "ensure_running" and i[0].args["name"] == "chrome"
    i, _ = planner.plan("create a folder Demo on my desktop, then create a file a.txt in documents with content hi", c)
    assert [x.tool for x in i] == ["create_folder", "create_file"]


def test_unmapped_is_reported_not_guessed():
    i, un = planner.plan("write me a poem about the sea", Context())
    assert not i and un


def test_control_words():
    for w in ("stop", "Stop.", "cancel", "hey mrx, stop", "ruko", "रुको"):
        assert parse_control(w) == "cancel", w
    assert parse_control("pause") == "pause" and parse_control("continue") == "resume" and parse_control("retry that") == "retry"
    assert parse_control("stop the music player app please") is None
    assert parse_control("open chrome") is None


# ---- context handling ---------------------------------------------------------------------------------
async def test_context_references_it_and_same_folder(rt, home):
    auto_confirm(rt)
    await say(rt, "create a folder called Reports on my desktop")
    assert (home / "Desktop/Reports").is_dir()
    await say(rt, "create a file notes.txt inside it")
    assert (home / "Desktop/Reports/notes.txt").is_file()
    r, t = await say(rt, "delete it")   # 'it' = the last file
    assert t.outcome == "COMPLETED" and not (home / "Desktop/Reports/notes.txt").exists()


async def test_ordinal_reference_to_last_list(rt, home):
    for n in ("a.txt", "b.txt", "c.txt"):
        (home / "Documents" / n).write_text(n)
    await say(rt, "list files in documents")
    auto_confirm(rt)
    r, t = await say(rt, "delete the second one")
    assert t.outcome == "COMPLETED" and not (home / "Documents/b.txt").exists() and (home / "Documents/a.txt").exists()


# ---- offline engine executes for real & reports honestly ------------------------------------------------
async def test_create_folder_end_to_end(rt, home):
    events = []
    rt.bus.subscribe("*", lambda e: events.append(e["type"]))
    r, t = await say(rt, "Create a folder called Project X on my desktop")
    assert (home / "Desktop/Project X").is_dir()
    assert t.status == "COMPLETED" and t.outcome == "COMPLETED"
    assert any(s["status"] == "done" for s in t.steps)
    assert {"agent.started", "task.updated", "task.step", "tool.started", "tool.completed", "agent.message", "task.completed"} <= set(events)
    assert t.progress == 1.0 and t.completed_at and t.started_at


async def test_failure_reported_honestly(rt, home):
    auto_confirm(rt)   # unknown executables need confirmation; the user approves, the launch itself then fails
    r, t = await say(rt, "open definitely-not-an-installed-app-xyz")
    assert t.status == "FAILED" and t.outcome == "FAILED" and t.error
    msg = rt.db.one("SELECT content FROM messages WHERE role='assistant' ORDER BY id DESC")["content"]
    assert msg.startswith("✗") and "✓" not in msg


async def test_partial_completion(rt, home):
    auto_confirm(rt)
    r, t = await say(rt, "create a folder OkDir on my desktop and open definitely-not-an-app-xyz")
    assert t.outcome == "PARTIALLY_COMPLETED" and (home / "Desktop/OkDir").is_dir()


async def test_unmapped_request_says_it_cannot_and_does_not_fake(rt):
    r, t = await say(rt, "write me a haiku about clouds")
    msg = t.result
    assert "can't" in msg and "ANTHROPIC_API_KEY" in msg


async def test_declined_delete_leaves_file_and_reports(rt, home):
    (home / "keep.txt").write_text("x")
    auto_confirm(rt, approve=False)
    r, t = await say(rt, "delete keep.txt")
    assert (home / "keep.txt").exists()
    assert t.outcome == "FAILED" and "declined" in t.error


# ---- task control ----------------------------------------------------------------------------------------
async def test_stop_cancels_running_task(rt):
    from mrx.tools.registry import Tool
    started = asyncio.Event()

    async def slow(rt_):
        started.set()
        await asyncio.sleep(30)
    rt.registry.register(Tool("slow_op", "d", {}, [], slow))
    prov = ScriptedProvider([Turn("", [ToolUse("1", "slow_op", {})], "tool_use")])
    rt.agent.provider = prov
    r = await rt.agent.submit("do the slow thing", "c1")
    await asyncio.wait_for(started.wait(), 3)
    c = await rt.agent.submit("stop", "c1")
    assert c["control"] == "cancel" and r["task_id"] in c["affected"]
    t = rt.agent.tasks.tasks[r["task_id"]]
    await asyncio.sleep(0.1)
    assert t.status == "CANCELLED"


async def test_pause_resume_and_retry(rt, home):
    tm = rt.agent.tasks
    t = tm.create("x")
    await tm.set(t, status="RUNNING")
    assert tm.pause(t.id)
    waiter = asyncio.create_task(tm.checkpoint(t))
    await asyncio.sleep(0.05)
    assert t.status == "WAITING" and not waiter.done()
    assert tm.resume(t.id)
    await asyncio.wait_for(waiter, 1)
    assert t.status == "RUNNING"

    r, t1 = await say(rt, "create a folder Retry1 on my desktop", "rc")
    (home / "Desktop/Retry1").rmdir()
    c = await rt.agent.submit("retry that", "rc")
    t2 = await wait_task(rt, c["task_id"])
    assert (home / "Desktop/Retry1").is_dir() and t2.id != t1.id


# ---- LLM engine (scripted provider) ----------------------------------------------------------------------
async def test_llm_loop_parallel_tools_and_verification(rt, home):
    prov = ScriptedProvider([
        Turn("On it.", [ToolUse("a", "create_folder", {"path": "Desktop/One"}), ToolUse("b", "create_folder", {"path": "Desktop/Two"})], "tool_use"),
        Turn("Both folders exist.", [], "end_turn")])
    rt.agent.provider = prov
    deltas = []
    rt.bus.subscribe("agent.text_delta", lambda e: deltas.append(e["data"]["text"]))
    r, t = await say(rt, "make two folders One and Two on the desktop")
    assert (home / "Desktop/One").is_dir() and (home / "Desktop/Two").is_dir()
    assert t.outcome == "COMPLETED" and t.result == "Both folders exist." and deltas
    results = prov.calls[-1]["content"]
    assert len(results) == 2 and all(not x["is_error"] for x in results)  # both results in ONE user message


async def test_llm_recovers_by_changing_strategy(rt, home):
    auto_confirm(rt)
    prov = ScriptedProvider([
        Turn("", [ToolUse("1", "open_application", {"name": "no-such-app-zzz"})], "tool_use"),
        Turn("", [ToolUse("2", "create_folder", {"path": "Desktop/Fallback"})], "tool_use"),
        Turn("The app is not installed, so I created a folder instead.", [], "end_turn")])
    rt.agent.provider = prov
    r, t = await say(rt, "open the thing")
    assert prov.calls[1]["content"][0]["is_error"] is True   # the failure was shown to the model
    assert t.outcome == "PARTIALLY_COMPLETED"                  # status computed from tool results, not model words


async def test_llm_step_limit_prevents_infinite_loop(rt):
    rt.settings.update({"ai": {"max_steps": 3}})
    prov = ScriptedProvider([Turn("", [ToolUse(str(i), "list_directory", {})], "tool_use") for i in range(10)])
    rt.agent.provider = prov
    r, t = await say(rt, "loop forever")
    assert len(prov.calls) == 3 and "Stopped after 3 steps" in t.result


async def test_llm_failure_before_any_tool_falls_back_to_local(rt, home):
    from mrx.agent.llm import LLMError, LLMProvider

    class Down(LLMProvider):
        def available(self): return True, ""
        async def stream_turn(self, *a, **k): raise LLMError("Cannot reach the AI provider (offline?).")
        def tool_result_message(self, r): return {}
    rt.agent.provider = Down()
    r, t = await say(rt, "create a folder LocalOnly on my desktop")
    assert (home / "Desktop/LocalOnly").is_dir() and t.outcome == "COMPLETED"


async def test_unverified_action_is_not_reported_as_verified(rt, home):
    from mrx.tools.registry import Outcome, Tool
    rt.registry.register(Tool("fire_and_forget", "d", {}, [], lambda rt_: Outcome({"sent": True}, None, "event sent")))
    prov = ScriptedProvider([Turn("", [ToolUse("1", "fire_and_forget", {})], "tool_use"), Turn("Sent.", [], "end_turn")])
    rt.agent.provider = prov
    r, t = await say(rt, "do it")
    import json
    payload = json.loads(prov.calls[1]["content"][0]["content"])
    assert payload["success"] is True and payload["verified"] is None
    assert t.steps[-1]["status"] == "sent"   # UI shows 'sent', not a green verified tick
