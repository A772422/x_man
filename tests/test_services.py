import asyncio
import json

import httpx
import pytest

from conftest import auto_confirm, wait_task
from mrx.services import news


RSS = """<rss version="2.0"><channel><item><title>Alpha &amp; Beta</title><link>https://ex.com/a</link>
<pubDate>Tue, 30 Sep 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;Hi&lt;/b&gt; there</description></item>
<item><title>Old story</title><link>https://ex.com/b</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item></channel></rss>"""


def test_parse_feed_and_xxe_refusal():
    items = news.parse_feed(RSS, "Ex")
    assert items[0]["title"] == "Alpha & Beta" and items[0]["summary"] == "Hi there"
    with pytest.raises(ValueError):
        news.parse_feed('<!DOCTYPE x [<!ENTITY a "b">]><rss/>', "Ex")


class FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler): self.h = handler
    async def handle_async_request(self, request): return self.h(request)


async def test_news_freshness_and_attribution(rt):
    news._cache.clear()
    rt.http = httpx.AsyncClient(transport=FakeTransport(lambda r: httpx.Response(200, text=RSS)))
    res = await rt.registry.execute("get_live_news", {"category": "Science"})
    d = res.result
    assert d["retrieved_at"] and d["articles"][0]["source"] == "BBC Science"
    fresh = {a["title"]: a["freshness"] for a in d["articles"]}
    assert fresh["Old story"] == "older article"           # old news is labelled, not passed off as current
    assert all(a["claim_status"] == "unclassified" for a in d["articles"])


async def test_news_provider_failure_is_reported_not_faked(rt):
    news._cache.clear()
    rt.http = httpx.AsyncClient(transport=FakeTransport(lambda r: httpx.Response(503)))
    res = await rt.registry.execute("get_live_news", {"category": "World"})
    assert not res.success and "currently unavailable" in res.error and res.result is None
    assert rt.status()["services"]["news"]["state"] == "OFFLINE"


async def test_maps_layers_and_unavailable(rt):
    def h(req):
        if "earthquake" in str(req.url):
            return httpx.Response(200, json={"metadata": {"generated": 1790000000000}, "features": [
                {"geometry": {"coordinates": [10.5, 20.5, 8.0]}, "properties": {"mag": 4.2, "place": "X", "time": 1, "url": "u"}}]})
        return httpx.Response(500)
    rt.http = httpx.AsyncClient(transport=FakeTransport(h))
    r = await rt.registry.execute("get_world_map_data", {"layer": "earthquakes"})
    assert r.result["provider"] == "USGS" and r.result["data_updated"] and r.result["features"][0]["lat"] == 20.5
    r = await rt.registry.execute("get_world_map_data", {"layer": "traffic"})
    assert not r.success and "unavailable" in r.error
    r = await rt.registry.execute("get_world_map_data", {"layer": "weather", "lat": 1.0, "lon": 2.0})
    assert not r.success   # provider 500 → failure, not fake weather


# ---- YouTube: verification is driven by the UI's real player state ------------------------------------------
async def test_youtube_requires_ui_and_verifies_playback(rt):
    r = await rt.registry.execute("play_youtube", {"video_id": "dQw4w9WgXcQ"})
    assert not r.success and "not open" in r.error
    rt.ui_attached(1)

    async def ui():   # stands in for the browser UI's IFrame player
        ev = await rt.bus.wait_for("youtube.load", timeout=3)
        await rt.bus.emit("youtube.state", {"video_id": ev["data"]["video_id"], "state": "playing", "time": 0, "volume": 80})
    u = asyncio.create_task(ui())
    await asyncio.sleep(0)
    r = await rt.registry.execute("play_youtube", {"url": "https://youtu.be/dQw4w9WgXcQ"})
    await u
    assert r.success and r.verified and r.result["video_id"] == "dQw4w9WgXcQ"


async def test_youtube_unverified_when_player_never_plays(rt):
    rt.ui_attached(1)
    from mrx.services import youtube
    async def quick(rt_, pred, timeout=15.0):
        return await rt_.bus.wait_for("youtube.state", lambda e: pred(e["data"]), 0.2)
    youtube._await_state = quick
    r = await rt.registry.execute("play_youtube", {"video_id": "dQw4w9WgXcQ"})
    assert not r.success and "never reported" in r.error


async def test_youtube_search_not_configured(rt):
    r = await rt.registry.execute("youtube_search", {"query": "x"})
    assert not r.success and "YOUTUBE_API_KEY" in r.error


# ---- Email: draft ≠ send -------------------------------------------------------------------------------------------
async def test_email_unconfigured(rt):
    r = await rt.registry.execute("list_emails", {})
    assert r.status == "unavailable" and "MRX_EMAIL_ADDRESS" in r.error


async def test_email_draft_then_send_requires_confirmation(rt, monkeypatch):
    for k, v in dict(MRX_EMAIL_ADDRESS="me@example.com", MRX_EMAIL_PASSWORD="pw", MRX_IMAP_HOST="i", MRX_SMTP_HOST="s").items():
        monkeypatch.setenv(k, v)
    sent = []
    monkeypatch.setattr(type(rt.email), "send", lambda self, msg: sent.append(msg) or {})
    r = await rt.registry.execute("draft_email", {"to": "john@example.com", "subject": "Docs", "body": "I'll send the documents tomorrow."})
    assert r.success and not sent                     # drafting never sends
    did = r.result["draft_id"]
    auto_confirm(rt, approve=False)
    r = await rt.registry.execute("send_email", {"draft_id": did})
    assert r.status == "declined" and not sent
    rt.bus._handlers.clear()
    auto_confirm(rt, approve=True)
    r = await rt.registry.execute("send_email", {"draft_id": did})
    assert r.success and len(sent) == 1 and sent[0]["To"] == "john@example.com"
    assert (await rt.registry.execute("send_email", {"draft_id": did})).success is False   # no double send


async def test_email_send_refused_recipients_is_failure(rt, monkeypatch):
    for k, v in dict(MRX_EMAIL_ADDRESS="me@example.com", MRX_EMAIL_PASSWORD="pw", MRX_IMAP_HOST="i", MRX_SMTP_HOST="s").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(type(rt.email), "send", lambda self, msg: {"bad@example.com": (550, b"no such user")})
    rt.settings.update({"email": {"auto_send": True}})   # explicit opt-in: no prompt
    d = await rt.registry.execute("draft_email", {"to": "bad@example.com", "subject": "s", "body": "b"})
    r = await asyncio.wait_for(rt.registry.execute("send_email", {"draft_id": d.result["draft_id"]}), 3)
    assert not r.success and "refused" in r.error


async def test_social_providers_unconfigured(rt):
    r = await rt.registry.execute("social_read", {"provider": "mastodon"})
    assert not r.success and "not connected" in r.error
