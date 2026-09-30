"""HTTP + WebSocket API. Local-only: loopback host check, per-launch token, same-origin check."""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets as pysecrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..agent.language import detect_language
from ..core.config import DEFAULTS
from ..core.security import redact
from ..runtime import Runtime
from ..services import maps as maps_svc
from ..services import news as news_svc
from ..services.memory import CATEGORIES
from ..tools import system as system_tools
from ..tools.registry import ToolError

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
SECRET_NAMES = ["ANTHROPIC_API_KEY", "YOUTUBE_API_KEY", "MRX_EMAIL_ADDRESS", "MRX_EMAIL_PASSWORD", "MRX_IMAP_HOST",
                "MRX_SMTP_HOST", "MRX_MASTODON_URL", "MRX_MASTODON_TOKEN"]


def create_app(rt: Runtime | None = None, token: str | None = None) -> FastAPI:
    rt = rt or Runtime()
    token = token or __import__("os").environ.get("MRX_TOKEN") or pysecrets.token_urlsafe(24)
    bg: list[asyncio.Task] = []

    async def stats_loop():
        cooldown: dict[str, float] = {}
        while True:
            await asyncio.sleep(1.0)
            if rt.ui_clients() == 0:  # no viewer → no polling
                continue
            try:
                s = await asyncio.to_thread(system_tools.system_stats, rt)
            except Exception:
                continue
            await rt.bus.emit("system.stats", s)
            alerts = []
            if s["ram"]["percent"] > 92:
                alerts.append(("ram", f"RAM usage is {s['ram']['percent']}%"))
            if s["cpu_percent"] > 95:
                alerts.append(("cpu", f"CPU usage is {s['cpu_percent']}%"))
            if s.get("disk") and s["disk"]["percent"] > 95:
                alerts.append(("disk", f"Disk is {s['disk']['percent']}% full"))
            b = s.get("battery")
            if b and b["percent"] < 15 and not b["plugged"]:
                alerts.append(("battery", f"Battery low: {b['percent']}%"))
            for key, msg in alerts:
                if time.time() - cooldown.get(key, 0) > 300:
                    cooldown[key] = time.time()
                    await rt.bus.emit("system.alert", {"kind": key, "message": msg})

    async def scan_loop():
        while True:
            await asyncio.sleep(30)
            iv = int(rt.settings.get("network.scan_interval_s", 0) or 0)
            if iv > 0 and not rt.radar.scanning:
                await _background_scan()
                await asyncio.sleep(max(iv - 30, 0))

    async def _background_scan():
        try:
            await rt.radar.scan()
        except Exception as e:
            await rt.bus.emit("network.scan_failed", {"error": str(e)})

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bg.extend([asyncio.create_task(stats_loop()), asyncio.create_task(scan_loop())])
        yield
        for t in bg:
            t.cancel()
        await rt.close()

    app = FastAPI(title="M.R.X.", version="0.1.0", lifespan=lifespan)
    app.state.rt, app.state.token = rt, token

    # ---------------------------------------------------------------- security guard
    def host_ok(request) -> bool:
        return request.headers.get("host", "").rsplit(":", 1)[0].lower() in ALLOWED_HOSTS or \
            request.headers.get("host", "").lower() in ALLOWED_HOSTS

    def origin_ok(request) -> bool:
        o = request.headers.get("origin")
        if not o:
            return True
        return o.split("://", 1)[-1].lower() == request.headers.get("host", "").lower()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not host_ok(request):
            return JSONResponse({"error": "forbidden host"}, status_code=403)
        if request.url.path.startswith("/api"):
            if not origin_ok(request):
                return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
            if not hmac.compare_digest(request.headers.get("x-mrx-token", ""), token):
                return JSONResponse({"error": "missing or invalid token"}, status_code=401)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def ok(data=None, **kw):
        return {"ok": True, **(data if isinstance(data, dict) else {"data": data} if data is not None else {}), **kw}

    # ---------------------------------------------------------------- UI
    @app.get("/", response_class=HTMLResponse)
    async def index():
        html = (UI_DIR / "index.html").read_text("utf-8")
        return HTMLResponse(html.replace("__MRX_TOKEN__", token))

    # ---------------------------------------------------------------- chat / voice
    @app.post("/api/chat")
    async def chat(body: dict):
        r = await rt.agent.submit(body.get("text", ""), body.get("conversation_id"), body.get("source", "text"))
        if "error" in r:
            raise HTTPException(400, r["error"])
        return ok(r)

    @app.post("/api/chat/stream")
    async def chat_stream(body: dict):
        q = rt.bus.open_queue()
        r = await rt.agent.submit(body.get("text", ""), body.get("conversation_id"), body.get("source", "text"))
        if "error" in r:
            rt.bus.close_queue(q)
            raise HTTPException(400, r["error"])
        tid = r.get("task_id")

        async def gen():
            try:
                yield f"event: accepted\ndata: {json.dumps(r)}\n\n"
                if not tid:  # control command: single message
                    ev = await rt.bus.wait_for("agent.message", timeout=3)
                    yield f"event: message\ndata: {json.dumps(ev)}\n\n"
                    return
                while True:
                    ev = await asyncio.wait_for(q.get(), 300)
                    if ev.get("task_id") == tid or ev["type"] == "confirm.required":
                        yield f"event: {ev['type']}\ndata: {json.dumps(redact(ev), default=str)}\n\n"
                        t = rt.agent.tasks.tasks.get(tid)
                        if ev["type"] == "agent.message" and t and t.status in ("COMPLETED", "FAILED", "CANCELLED"):
                            return
            except asyncio.TimeoutError:
                yield "event: timeout\ndata: {}\n\n"
            finally:
                rt.bus.close_queue(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/voice/command")
    async def voice_command(body: dict):
        r = await rt.agent.submit(body.get("text", ""), body.get("conversation_id"), "voice")
        if "error" in r:
            raise HTTPException(400, r["error"])
        return ok(r)

    @app.post("/api/voice/detect-language")
    async def voice_lang(body: dict):
        return ok(detect_language(body.get("text", "")))

    @app.get("/api/voice/config")
    async def voice_cfg():
        return ok(rt.settings.get("voice"), stt="browser Web Speech API (streaming, multilingual)",
                  tts="browser SpeechSynthesis", note="Speech runs in the browser; audio is handled by your browser's speech provider.")

    @app.get("/api/conversations/{cid}/messages")
    async def messages(cid: str, limit: int = 100):
        return ok(messages=rt.db.query("SELECT role,content,lang,created_at FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (cid, limit))[::-1])

    # ---------------------------------------------------------------- tasks
    @app.get("/api/tasks")
    async def tasks_list():
        return ok(tasks=rt.agent.tasks.list())

    @app.get("/api/tasks/{tid}")
    async def task_get(tid: str):
        t = rt.agent.tasks.tasks.get(tid)
        if not t:
            raise HTTPException(404, "no such task")
        return ok(task=t.public())

    @app.post("/api/tasks/{tid}/{action}")
    async def task_action(tid: str, action: str):
        tm = rt.agent.tasks
        t = tm.tasks.get(tid)
        if not t:
            raise HTTPException(404, "no such task")
        if action == "pause":
            return ok(done=tm.pause(tid))
        if action == "resume":
            return ok(done=tm.resume(tid))
        if action == "cancel":
            return ok(done=await tm.cancel(tid))
        if action == "retry":
            return ok(await rt.agent.submit(t.command, t.conversation_id or None))
        raise HTTPException(400, "action must be pause|resume|cancel|retry")

    # ---------------------------------------------------------------- tools & confirmations
    @app.get("/api/tools")
    async def tools_list():
        return ok(tools=rt.registry.describe(), plugins=[{"name": p.name, "version": p.version, "description": p.description,
                                                          "permissions": p.permissions, "events": p.events, "builtin": p.builtin,
                                                          "enabled": p.name not in (rt.settings.get("plugins.disabled", []) or [])}
                                                         for p in rt.plugins.values()])

    @app.post("/api/tools/{name}")
    async def tool_run(name: str, body: dict | None = None):
        res = await rt.registry.execute(name, (body or {}).get("args", {}), (body or {}).get("task_id"))
        return ok(result=res.to_dict())

    @app.post("/api/plugins/{name}/{state}")
    async def plugin_toggle(name: str, state: str):
        try:
            rt.set_plugin_enabled(name, state == "enable")
        except KeyError:
            raise HTTPException(404, "no such plugin")
        return ok()

    @app.get("/api/confirmations")
    async def confirmations():
        return ok(pending=list(rt.registry.confirmations.pending.values()))

    @app.post("/api/confirmations/{cid}")
    async def confirm(cid: str, body: dict):
        if not rt.registry.confirmations.resolve(cid, bool(body.get("approved"))):
            raise HTTPException(404, "no such pending confirmation")
        return ok()

    # ---------------------------------------------------------------- memory
    @app.get("/api/memory")
    async def memory_list(q: str = "", category: str | None = None):
        if q:
            return ok(memories=rt.memory.search(q, 20, category), categories=CATEGORIES)
        return ok(memories=rt.memory.list(category), categories=CATEGORIES)

    @app.post("/api/memory")
    async def memory_add(body: dict):
        try:
            return ok(memory=rt.memory.remember(body["content"], body.get("category", "long_term"), body.get("key"), body.get("tags", "")))
        except ToolError as e:
            raise HTTPException(400, str(e))

    @app.patch("/api/memory/{mid}")
    async def memory_update(mid: int, body: dict):
        try:
            return ok(memory=rt.memory.update(mid, body.get("content"), body.get("tags"), body.get("key")))
        except ToolError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/memory/{mid}")
    async def memory_delete(mid: int):
        return ok(deleted=rt.memory.forget(mid))

    @app.delete("/api/memory")
    async def memory_clear(category: str | None = None):
        return ok(cleared=rt.memory.clear(category))

    # ---------------------------------------------------------------- files (goes through the audited tool layer)
    @app.get("/api/files/list")
    async def files_list(path: str = "~"):
        res = await rt.registry.execute("list_directory", {"path": path})
        if not res.success:
            raise HTTPException(400, res.error)
        return ok(res.result)

    @app.get("/api/files/read")
    async def files_read(path: str):
        res = await rt.registry.execute("read_file", {"path": path})
        if not res.success:
            raise HTTPException(400, res.error)
        return ok(res.result)

    # ---------------------------------------------------------------- browser
    @app.get("/api/browser/state")
    async def browser_state():
        return ok(await rt.browser.state())

    @app.get("/api/browser/screenshot")
    async def browser_shot():
        if not rt.browser.running:
            raise HTTPException(409, "browser is not running")
        page = await rt.browser.page()
        return Response(await page.screenshot(type="png"), media_type="image/png")

    # ---------------------------------------------------------------- email
    @app.get("/api/email/status")
    async def email_status():
        c, why = rt.email.configured()
        return ok(configured=c, reason=why, auto_send=rt.settings.get("email.auto_send", False),
                  drafts=rt.db.query("SELECT id,kind,provider,status,created_at,payload FROM drafts ORDER BY id DESC LIMIT 30"))

    # ---------------------------------------------------------------- news / maps
    @app.get("/api/news")
    async def news(category: str = "World", limit: int = 20):
        res = await rt.registry.execute("get_live_news", {"category": category, "limit": limit})
        if not res.success:
            return JSONResponse({"ok": False, "error": res.error, "unavailable": True}, status_code=503)
        return ok(res.result)

    @app.get("/api/news/categories")
    async def news_cats():
        return ok(categories=list((rt.settings.get("news.feeds") or {}).keys()))

    @app.get("/api/maps/layers")
    async def map_layers():
        return ok(layers=maps_svc.LAYERS, unavailable=maps_svc.UNAVAILABLE_LAYERS)

    @app.get("/api/maps/{layer}")
    async def map_layer(layer: str, request: Request):
        q = dict(request.query_params)
        args = {"layer": layer, **{k: (float(v) if k in ("lat", "lon") else v) for k, v in q.items() if k in ("period", "lat", "lon", "query")}}
        if layer == "flights":
            try:
                data = await maps_svc.flights(rt, float(q["south"]), float(q["west"]), float(q["north"]), float(q["east"]))
            except (KeyError, ValueError):
                raise HTTPException(400, "flights needs south, west, north, east")
            except ToolError as e:
                return JSONResponse({"ok": False, "error": str(e), "unavailable": True}, status_code=503)
            except Exception as e:
                return JSONResponse({"ok": False, "error": f"Live flight data is unavailable: {e}", "unavailable": True}, status_code=503)
            return ok(data)
        res = await rt.registry.execute("get_world_map_data", args)
        if not res.success:
            return JSONResponse({"ok": False, "error": res.error, "unavailable": True}, status_code=503)
        return ok(res.result)

    # ---------------------------------------------------------------- network radar
    @app.get("/api/network/devices")
    async def net_devices():
        return ok(last=rt.radar.last, known=rt.radar.known_devices(), scanning=rt.radar.scanning,
                  names=rt.settings.get("network.device_names", {}))

    @app.post("/api/network/scan")
    async def net_scan(body: dict | None = None):
        if rt.radar.scanning:
            raise HTTPException(409, "a scan is already running")
        asyncio.create_task(_background_scan())
        return ok(started=True)

    @app.post("/api/network/oui/update")
    async def oui_update():
        try:
            return ok(entries=await rt.radar.update_oui())
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Could not download the IEEE registry: {e}"}, status_code=502)

    # ---------------------------------------------------------------- system / settings / secrets / audit
    @app.get("/api/system/stats")
    async def sys_stats():
        return ok(await asyncio.to_thread(system_tools.system_stats, rt))

    @app.get("/api/system/status")
    async def sys_status():
        return ok(rt.status())

    @app.get("/api/system/probe")
    async def probe():
        async def one(name, url):
            t0 = time.perf_counter()
            try:
                r = await rt.http.head(url, timeout=6)
                return name, {"reachable": r.status_code < 500, "ms": int((time.perf_counter() - t0) * 1000)}
            except Exception as e:
                return name, {"reachable": False, "error": type(e).__name__}
        res = await asyncio.gather(one("internet", "https://www.google.com/generate_204"), one("news", "https://feeds.bbci.co.uk/news/rss.xml"),
                                   one("maps", "https://earthquake.usgs.gov/"), one("ai", "https://api.anthropic.com/"))
        return ok(dict(res))

    @app.post("/api/ai/test")
    async def ai_test():
        """Makes one tiny real request so the exact failure (bad key, model, network…) is visible."""
        from ..agent.llm import LLMError
        okp, why = rt.agent.provider.available()
        if not okp:
            return JSONResponse({"ok": False, "error": why}, status_code=200)
        t0 = time.perf_counter()

        async def _noop(_t: str) -> None: ...
        try:
            turn = await asyncio.wait_for(rt.agent.provider.stream_turn("Reply with the single word: ok",
                                                                        [{"role": "user", "content": "ping"}], [], _noop), 75)
        except LLMError as e:
            return {"ok": False, "error": str(e)}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "The AI provider did not answer within 75 s."}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return ok(reply=(turn.text or "")[:80], ms=int((time.perf_counter() - t0) * 1000), model=rt.settings.get("ai.model"))

    @app.get("/api/settings")
    async def settings_get():
        return ok(settings=rt.settings.all())

    @app.patch("/api/settings")
    async def settings_patch(body: dict):
        bad = [k for k in body if k not in DEFAULTS and k != "plugins"]
        if bad:
            raise HTTPException(400, f"unknown settings section(s): {bad}")
        return ok(settings=rt.settings.update(body))

    @app.get("/api/secrets")
    async def secrets_list():
        out = {}
        for n in SECRET_NAMES:
            where = rt.secrets.locate(n)
            out[n] = {"set": bool(where), "source": ", ".join(where) or "none"}
        return ok(secrets=out, backend=rt.secrets.backend)

    @app.post("/api/secrets/{name}")
    async def secrets_set(name: str, body: dict):
        if name not in SECRET_NAMES:
            raise HTTPException(400, "unknown secret name")
        value = str(body.get("value", "")).strip()
        if not value:
            raise HTTPException(400, "empty value")
        if rt.secrets.set(name, value):
            return ok(stored="keychain")
        if body.get("allow_file") and rt.secrets.set_file(name, value):
            return ok(stored="file")
        return JSONResponse({"ok": False, "error": "No OS keychain is available on this system.", "needs_file_fallback": True}, status_code=409)

    @app.delete("/api/secrets/{name}")
    async def secrets_del(name: str):
        if name not in SECRET_NAMES:
            raise HTTPException(400, "unknown secret name")
        rt.secrets.delete(name)
        return ok()

    @app.delete("/api/history")
    async def clear_history():
        for t in ("messages", "task_steps", "tasks", "conversations"):
            rt.db.execute(f"DELETE FROM {t}")
        rt.agent.tasks.tasks.clear()
        rt.agent.contexts.clear()
        return ok(cleared=True)

    @app.get("/api/audit")
    async def audit(limit: int = 100):
        return ok(calls=rt.db.query("SELECT ts,task_id,tool,arguments_redacted,status,duration_ms,result,error,verified FROM tool_calls ORDER BY id DESC LIMIT ?", (limit,)))

    @app.get("/api/events/recent")
    async def recent(limit: int = 200):
        return ok(events=[redact(e) for e in rt.bus.recent(limit)])

    # ---------------------------------------------------------------- websocket
    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        origin = ws.headers.get("origin")
        host = ws.headers.get("host", "")
        if host.rsplit(":", 1)[0].lower() not in ALLOWED_HOSTS or (origin and origin.split("://", 1)[-1].lower() != host.lower()) \
                or not hmac.compare_digest(ws.query_params.get("token", ""), token):
            await ws.close(code=4403)
            return
        await ws.accept()
        rt.ui_attached(1)
        q = rt.bus.open_queue()
        await ws.send_json({"type": "hello", "ts": time.time(), "data": {
            "status": rt.status(), "tasks": rt.agent.tasks.list(30), "pending_confirmations": list(rt.registry.confirmations.pending.values()),
            "voice": rt.settings.get("voice")}})

        async def pump():
            while True:
                ev = await q.get()
                await ws.send_text(json.dumps(redact(ev), default=str))

        pumper = asyncio.create_task(pump())
        try:
            while True:
                msg = await ws.receive_json()
                t = msg.get("type")
                if t == "chat":
                    await rt.agent.submit(msg.get("text", ""), msg.get("conversation_id"), msg.get("source", "text"))
                elif t == "confirm":
                    rt.registry.confirmations.resolve(msg.get("id", ""), bool(msg.get("approved")))
                elif t in ("youtube.state", "voice.state", "voice.input", "ui.state"):
                    await rt.bus.emit(t, msg.get("data", {}))
                elif t == "voice.interrupt":
                    await rt.bus.emit("voice.interrupt", {})
                elif t == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            pumper.cancel()
            rt.bus.close_queue(q)
            rt.ui_attached(-1)

    if UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=UI_DIR), name="static")
    return app
