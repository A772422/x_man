"""Persistent memory (long-term, preference, episodic, semantic) with local retrieval.

Retrieval is a local hashed word + character-n-gram vector search (no cloud, no model download). It is
lexical-semantic, not a neural embedding: it finds related wording and spelling, not deep paraphrase.
The `embedder` can be swapped for a real embedding model without touching callers."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Callable

from ..core.db import Database
from ..core.security import contains_secret
from ..tools.registry import Outcome, Tool, ToolError

DIM = 512
CATEGORIES = ["long_term", "preference", "episodic", "semantic", "project"]


def hash_embed(text: str) -> list[float]:
    t = re.sub(r"\s+", " ", text.lower())
    words = re.findall(r"\w+", t, flags=re.UNICODE)
    feats = [("w", w) for w in words]
    feats += [("b", a + " " + b) for a, b in zip(words, words[1:])]
    for w in words:
        w2 = f"<{w}>"
        feats += [("c", w2[i:i + 3]) for i in range(max(1, len(w2) - 2))]
    v = [0.0] * DIM
    for kind, f in feats:
        h = int(hashlib.md5(f"{kind}:{f}".encode()).hexdigest()[:8], 16)
        v[h % DIM] += (1.0 if kind != "c" else 0.35) * (1 if (h >> 20) & 1 else -1) * 1.0 + (0.0)
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class MemoryStore:
    def __init__(self, db: Database, embedder: Callable[[str], list[float]] = hash_embed):
        self.db, self.embed = db, embedder

    def remember(self, content: str, category: str = "long_term", key: str | None = None, tags: str = "") -> dict:
        if category not in CATEGORIES:
            raise ToolError(f"category must be one of {CATEGORIES}")
        if contains_secret(content) or contains_secret(key or ""):
            raise ToolError("that looks like a password, token or API key. Secrets are never stored in memory; "
                            "put them in the OS keychain or an environment variable instead.")
        now = time.time()
        if key:  # same key in same category updates instead of duplicating
            row = self.db.one("SELECT id FROM memories WHERE category=? AND key=?", (category, key))
            if row:
                return self.update(row["id"], content=content, tags=tags or None)
        mid = self.db.execute(
            "INSERT INTO memories(category,key,content,tags,embedding,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (category, key, content, tags, json.dumps(self.embed(f"{key or ''} {content} {tags}")), now, now))
        return self.get(mid)

    def get(self, mid: int) -> dict:
        r = self.db.one("SELECT id,category,key,content,tags,created_at,updated_at FROM memories WHERE id=?", (mid,))
        if not r:
            raise ToolError(f"memory {mid} does not exist")
        return r

    def update(self, mid: int, content: str | None = None, tags: str | None = None, key: str | None = None) -> dict:
        cur = self.get(mid)
        if content is not None and contains_secret(content):
            raise ToolError("that looks like a secret; not stored")
        new = {"content": content if content is not None else cur["content"],
               "tags": tags if tags is not None else cur["tags"], "key": key if key is not None else cur["key"]}
        self.db.execute("UPDATE memories SET content=?,tags=?,key=?,embedding=?,updated_at=? WHERE id=?",
                        (new["content"], new["tags"], new["key"],
                         json.dumps(self.embed(f"{new['key'] or ''} {new['content']} {new['tags']}")), time.time(), mid))
        return self.get(mid)

    def forget(self, mid: int) -> bool:
        self.db.execute("DELETE FROM memories WHERE id=?", (mid,))
        return self.db.one("SELECT 1 AS x FROM memories WHERE id=?", (mid,)) is None

    def clear(self, category: str | None = None) -> int:
        if category:
            return self.db.execute("DELETE FROM memories WHERE category=?", (category,))
        return self.db.execute("DELETE FROM memories")

    def list(self, category: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT id,category,key,content,tags,created_at,updated_at FROM memories"
        args: tuple = ()
        if category:
            q += " WHERE category=?"
            args = (category,)
        return self.db.query(q + " ORDER BY updated_at DESC LIMIT ?", args + (limit,))

    def search(self, query: str, limit: int = 5, category: str | None = None, min_score: float = 0.12) -> list[dict]:
        qv = self.embed(query)
        qwords = set(re.findall(r"\w+", query.lower()))
        rows = self.db.query("SELECT id,category,key,content,tags,embedding,updated_at FROM memories"
                             + (" WHERE category=?" if category else ""), (category,) if category else ())
        scored = []
        for r in rows:
            score = cosine(qv, json.loads(r.pop("embedding")))
            words = set(re.findall(r"\w+", f"{r['key'] or ''} {r['content']} {r['tags']}".lower()))
            score += 0.25 * (len(qwords & words) / max(len(qwords), 1))
            if score >= min_score:
                scored.append({**r, "score": round(score, 3)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def log_episode(self, text: str) -> None:
        if not contains_secret(text):
            self.remember(text[:500], category="episodic")


def _enabled(rt):
    return (bool(rt.settings.get("memory.enabled", True)), "memory is disabled in Settings → Memory")


def _remember(rt, content, category="long_term", key=None, tags=""):
    m = rt.memory.remember(content, category, key, tags)
    stored = rt.memory.get(m["id"])
    return Outcome(stored, stored["content"] == content, "re-read from database matches")


def _recall(rt, query, limit=5, category=None):
    res = rt.memory.search(query, limit, category)
    return {"query": query, "matches": res, "method": "local hashed n-gram similarity"}


def _update(rt, memory_id, content=None, tags=None):
    return rt.memory.update(memory_id, content, tags)


def _forget(rt, memory_id):
    ok = rt.memory.forget(memory_id)
    return Outcome({"memory_id": memory_id}, ok, "row no longer in database" if ok else "row still present")


def _list(rt, category=None, limit=100):
    return {"memories": rt.memory.list(category, limit)}


def tools() -> list[Tool]:
    S, I = {"type": "string"}, {"type": "integer"}
    cat = {"type": "string", "enum": CATEGORIES}
    return [
        Tool("remember", "Store a fact/preference/project note in long-term memory. Never store secrets.",
             {"content": S, "category": cat, "key": S, "tags": S}, ["content"], _remember, available=_enabled, plugin="memory", scope="memory"),
        Tool("recall_memory", "Search memory for information relevant to a query.", {"query": S, "limit": I, "category": cat},
             ["query"], _recall, available=_enabled, plugin="memory", scope="memory"),
        Tool("update_memory", "Update a stored memory by id.", {"memory_id": I, "content": S, "tags": S}, ["memory_id"], _update,
             available=_enabled, plugin="memory", scope="memory"),
        Tool("forget_memory", "Delete a memory by id.", {"memory_id": I}, ["memory_id"], _forget, risk=2, plugin="memory", scope="memory"),
        Tool("list_memories", "List stored memories.", {"category": cat, "limit": I}, [], _list, plugin="memory", scope="memory"),
    ]
