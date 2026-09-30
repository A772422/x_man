"""Provider-based social integration. Nothing is hard-coded to one platform. Ships a Mastodon provider that
uses an access token from an app the user created on their own instance (official API; no password)."""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod

from ..tools.registry import Outcome, Tool, ToolError


class SocialProvider(ABC):
    name = "abstract"

    def __init__(self, rt):
        self.rt = rt

    @abstractmethod
    def configured(self) -> tuple[bool, str]: ...
    @abstractmethod
    async def connect(self) -> dict: ...
    @abstractmethod
    async def read(self, limit: int) -> list[dict]: ...
    @abstractmethod
    async def search(self, query: str, limit: int) -> list[dict]: ...
    @abstractmethod
    async def publish(self, text: str) -> dict: ...
    @abstractmethod
    async def delete(self, post_id: str) -> bool: ...

    async def authenticate(self) -> dict:
        return await self.connect()

    async def create_draft(self, text: str) -> int:
        return self.rt.db.execute("INSERT INTO drafts(kind,provider,payload,status,created_at) VALUES('social',?,?,'draft',?)",
                                  (self.name, json.dumps({"text": text}), time.time()))


class Mastodon(SocialProvider):
    name = "mastodon"

    def _cfg(self):
        return (self.rt.secrets.get("MRX_MASTODON_URL") or "").rstrip("/"), self.rt.secrets.get("MRX_MASTODON_TOKEN")

    def configured(self):
        u, t = self._cfg()
        return (bool(u and t), "" if u and t else "set MRX_MASTODON_URL and MRX_MASTODON_TOKEN (access token from your instance's Development settings)")

    async def _req(self, method, path, **kw):
        u, t = self._cfg()
        r = await self.rt.http.request(method, f"{u}{path}", headers={"Authorization": f"Bearer {t}"}, timeout=20, **kw)
        if r.status_code >= 400:
            raise ToolError(f"Mastodon API {r.status_code}: {r.text[:150]}")
        return r.json() if r.content else {}

    async def connect(self):
        a = await self._req("GET", "/api/v1/accounts/verify_credentials")
        return {"account": a.get("acct"), "display_name": a.get("display_name")}

    @staticmethod
    def _post(s):
        import re
        return {"id": s["id"], "author": s["account"]["acct"], "text": re.sub(r"<[^>]+>", "", s["content"])[:500],
                "created_at": s["created_at"], "url": s.get("url")}

    async def read(self, limit):
        return [self._post(s) for s in await self._req("GET", "/api/v1/timelines/home", params={"limit": limit})]

    async def search(self, query, limit):
        r = await self._req("GET", "/api/v2/search", params={"q": query, "type": "statuses", "limit": limit})
        return [self._post(s) for s in r.get("statuses", [])]

    async def publish(self, text):
        s = await self._req("POST", "/api/v1/statuses", data={"status": text})
        return {"id": s["id"], "url": s.get("url")}

    async def delete(self, post_id):
        await self._req("DELETE", f"/api/v1/statuses/{post_id}")
        return True


def providers(rt) -> dict[str, SocialProvider]:
    return {"mastodon": Mastodon(rt)}


def _get(rt, name: str) -> SocialProvider:
    p = providers(rt).get(name)
    if not p:
        raise ToolError(f"unknown provider '{name}'. Available: {list(providers(rt))}")
    ok, why = p.configured()
    if not ok:
        raise ToolError(f"{name} is not connected: {why}")
    return p


async def social_providers(rt):
    return {"providers": [{"name": n, "connected": p.configured()[0], "hint": p.configured()[1]} for n, p in providers(rt).items()]}


async def social_read(rt, provider: str, limit: int = 10):
    return {"posts": await _get(rt, provider).read(limit)}


async def social_search(rt, provider: str, query: str, limit: int = 10):
    return {"posts": await _get(rt, provider).search(query, limit)}


async def social_create_draft(rt, provider: str, text: str):
    did = await _get(rt, provider).create_draft(text)
    return Outcome({"draft_id": did}, True, "draft saved locally; not published")


async def social_publish(rt, draft_id: int):
    row = rt.db.one("SELECT * FROM drafts WHERE id=? AND kind='social'", (draft_id,))
    if not row or row["status"] != "draft":
        raise ToolError("no such unpublished social draft")
    res = await _get(rt, row["provider"]).publish(json.loads(row["payload"])["text"])
    rt.db.execute("UPDATE drafts SET status='sent', sent_at=? WHERE id=?", (time.time(), draft_id))
    return Outcome({"draft_id": draft_id, **res}, bool(res.get("id")), "API returned the created post id" if res.get("id") else "API returned no id")


async def social_delete(rt, provider: str, post_id: str):
    ok = await _get(rt, provider).delete(post_id)
    return Outcome({"post_id": post_id}, ok, "API confirmed deletion")


def tools() -> list[Tool]:
    S = {"type": "string"}
    K = dict(plugin="social", scope="social")
    return [
        Tool("social_list_providers", "List social providers and whether each is connected.", {}, [], social_providers, **K),
        Tool("social_read", "Read the home timeline of a connected provider.", {"provider": S, "limit": {"type": "integer"}}, ["provider"], social_read, **K),
        Tool("social_search", "Search posts on a connected provider.", {"provider": S, "query": S, "limit": {"type": "integer"}}, ["provider", "query"], social_search, **K),
        Tool("social_create_draft", "Draft a post (not published).", {"provider": S, "text": S}, ["provider", "text"], social_create_draft, **K),
        Tool("social_publish", "Publish a draft post.", {"draft_id": {"type": "integer"}}, ["draft_id"], social_publish, risk=2, **K),
        Tool("social_delete", "Delete a post.", {"provider": S, "post_id": S}, ["provider", "post_id"], social_delete, risk=3, **K),
    ]
