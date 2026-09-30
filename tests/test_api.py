import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedProvider
from mrx.api.app import create_app
from mrx.agent.llm import ToolUse, Turn
from mrx.runtime import Runtime

TOK = "test-token"
H = {"x-mrx-token": TOK}


@pytest.fixture
def client(home):
    rt = Runtime(home / ".mrx")
    app = create_app(rt, TOK)
    with TestClient(app) as c:
        c.rt = rt
        yield c


def test_auth_host_and_origin_guards(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"x-mrx-token": "wrong"}).status_code == 401
    assert client.get("/api/tasks", headers=H).status_code == 200
    assert client.get("/api/tasks", headers={**H, "host": "evil.example.com"}).status_code == 403     # DNS-rebinding guard
    assert client.get("/api/tasks", headers={**H, "origin": "http://evil.example.com"}).status_code == 403   # CSRF guard
    r = client.get("/")
    assert r.status_code == 200 and TOK in r.text and "__MRX_TOKEN__" not in r.text


def test_websocket_requires_token_and_same_origin(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/events?token=bad"):
            pass
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/events?token={TOK}", headers={"origin": "http://evil.com"}):
            pass


def collect_until(ws, pred, limit=200):
    seen = []
    for _ in range(limit):
        ev = ws.receive_json()
        seen.append(ev)
        if pred(ev):
            return seen
    raise AssertionError("event not seen: " + str([e["type"] for e in seen][-10:]))


def test_chat_over_websocket_streams_realtime_events(client, home):
    with client.websocket_connect(f"/ws/events?token={TOK}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "engine" in hello["data"]["status"]
        ws.send_json({"type": "chat", "text": "create a folder called WSDemo on my desktop"})
        seen = collect_until(ws, lambda e: e["type"] == "task.completed")
        types = [e["type"] for e in seen]
        assert types.index("agent.started") < types.index("tool.started") < types.index("tool.completed") < types.index("task.completed")
        assert (home / "Desktop/WSDemo").is_dir()


def test_confirmation_roundtrip_over_websocket(client, home):
    (home / "Documents").mkdir(exist_ok=True)
    (home / "Documents/zap.txt").write_text("x")
    with client.websocket_connect(f"/ws/events?token={TOK}") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "delete zap.txt"})
        seen = collect_until(ws, lambda e: e["type"] == "confirm.required")
        cid = seen[-1]["data"]["id"]
        assert client.get("/api/confirmations", headers=H).json()["pending"][0]["id"] == cid
        assert (home / "Documents/zap.txt").exists() or (home / "zap.txt").exists()
        ws.send_json({"type": "confirm", "id": cid, "approved": False})
        collect_until(ws, lambda e: e["type"] == "task.updated" and e["data"]["status"] in ("FAILED", "COMPLETED"))
        assert (home / "Documents/zap.txt").exists()


def test_ui_events_feed_backend_youtube_state(client):
    with client.websocket_connect(f"/ws/events?token={TOK}") as ws:
        ws.receive_json()
        assert client.rt.ui_clients() == 1
        ws.send_json({"type": "youtube.state", "data": {"video_id": "abc", "state": "playing", "time": 3}})
        collect_until(ws, lambda e: e["type"] == "youtube.state")
        assert client.rt.youtube_state["state"] == "playing"
    assert client.rt.ui_clients() == 0


def test_rest_chat_tasks_and_sse(client, home):
    r = client.post("/api/chat", headers=H, json={"text": "create a folder called RestDemo on my desktop"})
    tid = r.json()["task_id"]
    for _ in range(100):
        t = client.get(f"/api/tasks/{tid}", headers=H).json()["task"]
        if t["status"] in ("COMPLETED", "FAILED"):
            break
        import time; time.sleep(0.05)
    assert t["status"] == "COMPLETED" and (home / "Desktop/RestDemo").is_dir()
    assert client.post("/api/chat", headers=H, json={"text": " "}).status_code == 400
    with client.stream("POST", "/api/chat/stream", headers=H, json={"text": "create a folder SseDemo on my desktop"}) as s:
        body = "".join(s.iter_text())
    assert "event: accepted" in body and "event: tool.completed" in body and "event: agent.message" in body


def test_memory_settings_secrets_audit_endpoints(client):
    r = client.post("/api/memory", headers=H, json={"content": "Assistant name is Jarvis", "category": "preference"})
    mid = r.json()["memory"]["id"]
    assert client.get("/api/memory?q=assistant name", headers=H).json()["memories"][0]["id"] == mid
    assert client.post("/api/memory", headers=H, json={"content": "password is hunter2"}).status_code == 400
    assert client.patch(f"/api/memory/{mid}", headers=H, json={"content": "Name: Friday"}).json()["memory"]["content"] == "Name: Friday"
    assert client.delete(f"/api/memory/{mid}", headers=H).json()["deleted"] is True

    assert client.patch("/api/settings", headers=H, json={"automation": {"retry_limit": 1}}).json()["settings"]["automation"]["retry_limit"] == 1
    assert client.patch("/api/settings", headers=H, json={"bogus": {}}).status_code == 400

    s = client.get("/api/secrets", headers=H).json()
    assert s["secrets"]["ANTHROPIC_API_KEY"]["set"] is False and "value" not in json.dumps(s)
    assert client.post("/api/secrets/NOT_ALLOWED", headers=H, json={"value": "x"}).status_code == 400

    client.post("/api/tools/create_folder", headers=H, json={"args": {"path": "AuditMe"}})
    calls = client.get("/api/audit", headers=H).json()["calls"]
    assert calls[0]["tool"] == "create_folder" and calls[0]["status"] == "completed"


def test_tools_catalog_and_plugin_toggle(client):
    d = client.get("/api/tools", headers=H).json()
    names = {t["name"] for t in d["tools"]}
    assert {"open_application", "create_folder", "navigate", "scan_network", "get_live_news", "remember", "play_youtube", "draft_email"} <= names
    assert {p["name"] for p in d["plugins"]} >= {"filesystem", "browser", "email", "youtube"}
    assert client.post("/api/plugins/news/disable", headers=H).status_code == 200
    assert "get_live_news" not in {t["name"] for t in client.get("/api/tools", headers=H).json()["tools"]}
    client.post("/api/plugins/news/enable", headers=H)
    assert "get_live_news" in {t["name"] for t in client.get("/api/tools", headers=H).json()["tools"]}


def test_news_and_maps_unavailable_returns_503_not_fake_data(client):
    import httpx
    async def boom(*a, **k): raise httpx.ConnectError("offline")
    client.rt.http.get = boom
    r = client.get("/api/news?category=World", headers=H)
    assert r.status_code == 503 and r.json()["unavailable"] and "articles" not in r.json()
    r = client.get("/api/maps/earthquakes", headers=H)
    assert r.status_code == 503 and "features" not in r.json()


def test_system_status_and_stats(client):
    s = client.get("/api/system/stats", headers=H).json()
    assert 0 <= s["cpu_percent"] <= 100 and s["ram"]["total_gb"] > 0 and "gpu" in s
    st = client.get("/api/system/status", headers=H).json()
    assert st["services"]["ai"]["state"] == "DEGRADED" and st["engine"] == "local"
    assert st["services"]["filesystem"]["state"] == "LOCAL"


def test_plugin_loading_from_data_dir(home):
    d = home / ".mrx/plugins"
    d.mkdir(parents=True)
    (d / "hello.py").write_text('''
from mrx.plugins import Plugin
from mrx.tools.registry import Tool
def tools(): return [Tool("hello_world", "Say hi", {}, [], lambda rt: {"msg": "hi"})]
PLUGIN = Plugin("hello", "0.1", "demo", ["none"], tools)
''')
    (d / "broken.py").write_text("raise RuntimeError('boom')")
    rt = Runtime(home / ".mrx")
    assert "hello_world" in rt.registry.tools and rt.plugins["hello"].builtin is False and "filesystem" in rt.plugins
