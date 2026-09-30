"""Built-in YouTube player integration. Search uses the official YouTube Data API (needs YOUTUBE_API_KEY).
Playback happens in the M.R.X. UI through the official IFrame Player API; the UI reports its real player state
back over the event WebSocket, which is what playback verification is based on."""
from __future__ import annotations

import re
import time

from ..tools.registry import Outcome, Tool, ToolError

API = "https://www.googleapis.com/youtube/v3"


def parse_video_id(s: str) -> str | None:
    s = s.strip()
    if re.fullmatch(r"[\w\-]{11}", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([\w\-]{11})", s)
    return m.group(1) if m else None


def _key(rt) -> str | None:
    return rt.secrets.get("YOUTUBE_API_KEY")


async def search(rt, query: str, limit: int = 8) -> dict:
    key = _key(rt)
    if not key:
        raise ToolError("YouTube search is not configured: set the YOUTUBE_API_KEY environment variable "
                        "(YouTube Data API v3). You can still play a video by URL or ID.")
    r = await rt.http.get(f"{API}/search", timeout=15, params={"part": "snippet", "type": "video", "q": query,
                                                                "maxResults": limit, "key": key, "safeSearch": "moderate"})
    if r.status_code >= 400:
        raise ToolError(f"YouTube API error {r.status_code}: {r.json().get('error', {}).get('message', r.text[:120])}")
    items = r.json().get("items", [])
    ids = ",".join(i["id"]["videoId"] for i in items)
    details = {}
    if ids:
        d = await rt.http.get(f"{API}/videos", timeout=15, params={"part": "contentDetails,statistics", "id": ids, "key": key})
        if d.status_code < 400:
            details = {v["id"]: v for v in d.json().get("items", [])}
    res = []
    for i, it in enumerate(items, 1):
        vid, sn = it["id"]["videoId"], it["snippet"]
        dv = details.get(vid, {})
        res.append({"index": i, "video_id": vid, "title": sn["title"], "channel": sn["channelTitle"],
                    "published": sn["publishedAt"], "thumbnail": sn["thumbnails"].get("medium", {}).get("url"),
                    "duration": dv.get("contentDetails", {}).get("duration"),
                    "views": dv.get("statistics", {}).get("viewCount")})
    rt.db.pref_set("youtube.history", ([{"q": query, "ts": time.time()}] + rt.db.pref_get("youtube.history", []))[:30])
    return {"query": query, "results": res, "provider": "YouTube Data API v3"}


async def _await_state(rt, pred, timeout=15.0):
    return await rt.bus.wait_for("youtube.state", lambda e: pred(e["data"]), timeout)


def _need_ui(rt):
    if rt.ui_clients() == 0:
        raise ToolError("the M.R.X. interface is not open, so there is no player to control. Open it and try again.")


async def youtube_search(rt, query: str, limit: int = 8):
    res = await search(rt, query, limit)
    await rt.bus.emit("youtube.results", res)
    return res


async def play_youtube(rt, query: str | None = None, video_id: str | None = None, url: str | None = None, index: int = 1):
    _need_ui(rt)
    vid, meta = (video_id or (parse_video_id(url) if url else None)), {}
    if not vid:
        if not query:
            raise ToolError("provide a query, video_id or url")
        res = await search(rt, query, max(index, 5))
        await rt.bus.emit("youtube.results", res)
        if not res["results"] or index > len(res["results"]):
            raise ToolError(f"no result #{index} for '{query}'")
        meta = res["results"][index - 1]
        vid = meta["video_id"]
    await rt.bus.emit("ui.navigate", {"panel": "youtube"})
    await rt.bus.emit("youtube.load", {"video_id": vid, "meta": meta, "autoplay": True})
    ev = await _await_state(rt, lambda d: d.get("video_id") == vid and d.get("state") == "playing", 20)
    rt.youtube_state.update(video_id=vid)
    return Outcome({"video_id": vid, "title": meta.get("title"), "url": f"https://www.youtube.com/watch?v={vid}"},
                   ev is not None, "player reported state=playing" if ev else
                   "player never reported 'playing' within 20s (embedding may be blocked for this video, or autoplay was refused)")


async def youtube_control(rt, action: str, value: float | None = None):
    _need_ui(rt)
    preds = {"play": lambda d: d.get("state") == "playing", "pause": lambda d: d.get("state") == "paused",
             "seek": lambda d: value is not None and abs((d.get("time") or 0) - value) < 3,
             "volume": lambda d: value is not None and abs((d.get("volume") or 0) - value) <= 2}
    if action in ("seek", "volume") and value is None:
        raise ToolError(f"'{action}' needs a value")
    before = rt.youtube_state.get("video_id")
    await rt.bus.emit("youtube.command", {"action": action, "value": value})
    if action in preds:
        ev = await _await_state(rt, preds[action], 6)
        return Outcome({"action": action, "state": rt.youtube_state}, ev is not None,
                       "player state confirms" if ev else "player state did not change as requested")
    if action in ("next", "previous"):
        ev = await _await_state(rt, lambda d: d.get("video_id") not in (None, before), 8)
        return Outcome({"action": action, "state": rt.youtube_state}, ev is not None, "video changed" if ev else "video did not change")
    return Outcome({"action": action}, None, "command sent to player")


async def youtube_status(rt):
    return {"ui_connected": rt.ui_clients() > 0, "player": rt.youtube_state,
            "search_configured": bool(_key(rt))}


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("youtube_search", "Search YouTube and show results in the built-in player panel.", {"query": S, "limit": {"type": "integer"}}, ["query"], youtube_search, plugin="youtube", scope="youtube"),
        Tool("play_youtube", "Search (or use a URL/ID), load the video in the built-in player and start playback. Verified by the player's reported state.",
             {"query": S, "video_id": S, "url": S, "index": {"type": "integer"}}, [], play_youtube, plugin="youtube", scope="youtube", timeout_s=60),
        Tool("youtube_control", "Control the built-in player: play, pause, seek, volume, next, previous, fullscreen.",
             {"action": {"type": "string", "enum": ["play", "pause", "seek", "volume", "next", "previous", "fullscreen"]}, "value": {"type": "number"}}, ["action"], youtube_control, plugin="youtube", scope="youtube"),
        Tool("youtube_status", "Get the built-in player's current state.", {}, [], youtube_status, plugin="youtube", scope="youtube"),
    ]
