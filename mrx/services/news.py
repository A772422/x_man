"""Live news from real RSS/Atom feeds. Nothing is cached beyond 5 minutes, nothing is invented: if every
provider fails the tool fails."""
from __future__ import annotations

import asyncio
import email.utils
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape

from ..tools.registry import Outcome, Tool, ToolError

CACHE_TTL = 300
_cache: dict[str, tuple[float, dict]] = {}


def _text(e, *names):
    for n in names:
        for c in e:
            if c.tag.split("}")[-1] == n and (c.text or c.attrib.get("href")):
                return (c.text or "").strip() or c.attrib.get("href", "")
    return ""


def _parse_date(s: str) -> float | None:
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_feed(xml_text: str, source: str, limit: int = 20) -> list[dict]:
    if re.search(r"<!DOCTYPE|<!ENTITY", xml_text, re.I):
        raise ValueError("feed contains a DTD/entity declaration; refusing to parse")
    root = ET.fromstring(xml_text)
    items = [e for e in root.iter() if e.tag.split("}")[-1] in ("item", "entry")]
    out = []
    for it in items[:limit]:
        link = _text(it, "link")
        if not link:
            for c in it:
                if c.tag.split("}")[-1] == "link" and c.attrib.get("href"):
                    link = c.attrib["href"]
        pub = _parse_date(_text(it, "pubDate", "published", "updated", "date"))
        desc = re.sub(r"<[^>]+>", "", unescape(_text(it, "description", "summary")))[:280].strip()
        out.append({"title": unescape(_text(it, "title")), "link": link, "source": source, "summary": desc,
                    "published_ts": pub})
    return [o for o in out if o["title"] and o["link"]]


async def fetch_feed(rt, name: str, url: str) -> list[dict]:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]["items"]
    r = await rt.http.get(url, timeout=12, follow_redirects=True)
    r.raise_for_status()
    items = parse_feed(r.text, name)
    _cache[url] = (time.time(), {"items": items})
    return items


async def get_news(rt, category: str = "World", limit: int = 15):
    feeds = (rt.settings.get("news.feeds", {}) or {}).get(category)
    if not feeds:
        raise ToolError(f"unknown category '{category}'. Available: {', '.join((rt.settings.get('news.feeds') or {}).keys())}")
    results = await asyncio.gather(*[fetch_feed(rt, n, u) for n, u in feeds], return_exceptions=True)
    articles, status = [], []
    for (n, u), r in zip(feeds, results):
        if isinstance(r, Exception):
            status.append({"source": n, "ok": False, "error": f"{type(r).__name__}: {str(r)[:120]}"})
        else:
            status.append({"source": n, "ok": True, "count": len(r)})
            articles += r
    if not articles:
        raise ToolError("The news providers are currently unavailable: " + "; ".join(f"{s['source']}: {s.get('error')}" for s in status))
    now = time.time()
    articles.sort(key=lambda a: a["published_ts"] or 0, reverse=True)
    for a in articles:
        p = a["published_ts"]
        a["published"] = datetime.fromtimestamp(p, timezone.utc).isoformat() if p else None
        a["age_hours"] = round((now - p) / 3600, 1) if p else None
        a["freshness"] = "unknown date" if p is None else ("current (<48h)" if now - p < 172800 else "older article")
        a["claim_status"] = "unclassified"  # provenance labels (confirmed/official/reported…) are NOT auto-assigned
    retrieved = datetime.now(timezone.utc).isoformat()
    return {"category": category, "articles": articles[:limit], "retrieved_at": retrieved, "providers": status,
            "note": "Articles are listed as published by each source. M.R.X. does not verify claims."}


def tools() -> list[Tool]:
    return [Tool("get_live_news", "Fetch current headlines from real news feeds (with source, publish time and link).",
                 {"category": {"type": "string"}, "limit": {"type": "integer"}}, [], get_news,
                 plugin="news", scope="news", timeout_s=40)]
