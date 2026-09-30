"""Offline (no LLM) intent engine. Deterministic rules that map common commands to real tools.
It is intentionally conservative: if it cannot map a request it says so instead of guessing.
Anything open-ended (summarise, write, reason over content, complex multi-step flows) needs the LLM engine."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .context import Context, ORDINALS

FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
           "music": "Music", "videos": "Videos"}
LOC = r"(?:on|in|inside|into|under|at)\s+(?:my\s+|the\s+)?(desktop|documents|downloads|pictures|music|videos)(?:\s+folder)?"
ORD = "|".join(sorted(ORDINALS, key=len, reverse=True))
PRON = r"(?:it|that|this|that one|this one|the same (?:file|folder))"
VERBS = r"open|launch|start|close|quit|exit|create|make|delete|remove|rename|move|copy|search|find|play|go|navigate|scan|show|list|remember|recall|stop|check|take|download|read|run|khol|band|bana|chalao|dhundo|dikhao"


@dataclass
class Intent:
    kind: str = "call"                # call | ensure_running | say
    tool: str = ""
    args: dict = field(default_factory=dict)
    label: str = ""
    text: str = ""                    # for kind=say


def split_clauses(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", text.strip().rstrip("."))
    if re.match(r"if\b", t, re.I):  # a conditional stays one clause: "if X isn't running, open it"
        return [t]
    parts = re.split(r"\s*(?:;|,\s*then\s+|\band then\b|\bthen\b|\bafter that\b|,\s*and\s+(?=(?:" + VERBS + r")\b)|\s+and\s+(?=(?:" + VERBS + r")\b)|,\s+(?=(?:" + VERBS + r")\b))\s*", t, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def _unquote(s: str) -> str:
    return s.strip().strip("\"'“”‘’").strip()


def _name_and_loc(s: str) -> tuple[str, str | None]:
    m = re.search(LOC, s, re.I)
    loc = FOLDERS[m.group(1).lower()] if m else None
    name = s[:m.start()] if m else s
    name = re.sub(r"^(?:a|an|the|new)\s+", "", name.strip(), flags=re.I)
    name = re.sub(r"^(?:called|named|name)\s+", "", name.strip(), flags=re.I)
    return _unquote(name), loc


def _target(rest: str, ctx: Context) -> tuple[str | None, str]:
    """Resolve a path from text or from conversation context ('it', 'that', 'the second one')."""
    rest = rest.strip()
    if re.fullmatch(PRON, rest, re.I):
        return ctx.last_path, "the last file/folder"
    m = re.fullmatch(rf"(?:the\s+)?({ORD})(?:\s+(?:one|file|folder|result))?", rest, re.I)
    if m:
        item = ctx.pick(m.group(1))
        p = item and (item.get("path"))
        return p, f"{m.group(1)} item of the last list"
    name, loc = _name_and_loc(re.sub(r"^(?:the\s+)?(?:file|folder|directory)\s+", "", rest, flags=re.I))
    if not name:
        return None, ""
    if re.match(r"^(~|/|[A-Za-z]:[\\/])", name):
        return name, name
    if loc:
        return f"{loc}/{name}", f"{name} in {loc}"
    if ctx.last_folder and re.search(r"\bsame folder\b|\bthere\b", rest, re.I):
        return f"{ctx.last_folder}/{name}", name
    return name, name


SITES = {"youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
         "github": "https://github.com", "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com",
         "twitter": "https://x.com", "x": "https://x.com", "whatsapp": "https://web.whatsapp.com", "linkedin": "https://www.linkedin.com",
         "reddit": "https://www.reddit.com", "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com",
         "wikipedia": "https://www.wikipedia.org", "google maps": "https://www.google.com/maps", "maps": "https://www.google.com/maps",
         "google drive": "https://drive.google.com", "drive": "https://drive.google.com", "chatgpt": "https://chatgpt.com",
         "claude": "https://claude.ai", "outlook": "https://outlook.live.com", "stackoverflow": "https://stackoverflow.com"}

URL_RE = re.compile(r"((?:https?://)?(?:[\w\-]+\.)+(?:com|org|net|io|in|co|edu|gov|dev|app|ai|tv|me)(?:/\S*)?)", re.I)


def plan_clause(clause: str, ctx: Context) -> Intent | None:
    c = clause.strip()
    low = c.lower()

    if re.fullmatch(r"(?:hi+|hey+|hello+|hola|yo|namaste|namaskar|sat sri akal|salaam|assalam[a-z ]*|good (?:morning|afternoon|evening)|"
                    r"how are you|what'?s up|sup)(?:\s+(?:there|mrx|m\.?r\.?x\.?|buddy))?[!?. ]*", low) or \
            re.fullmatch(r"(?:hey|hi|hello|ok|okay)[, ]+m\.?r\.?x\.?[!?. ]*|(?:नमस्ते|हेलो|हैलो|सत श्री अकाल|ਸਤ ਸ੍ਰੀ ਅਕਾਲ)[!?. ]*", low):
        return Intent("say", text="Hello! I'm M.R.X. Tell me what to do — for example “open Chrome”, “create a folder called "
                                  "Project X on my desktop”, “scan my network” or “show today's news”.", label="greeting")
    if re.fullmatch(r"(?:help|what can you do|who are you|what are you)[?! .]*", low):
        return Intent("say", text="I'm M.R.X., an agent that operates your computer: apps, files, the browser, YouTube, memory, "
                                  "your local network, news and maps. Say what you want done and I'll do it and verify the result.",
                      label="greeting")

    m = re.match(r"(?:what(?:'s| is) the )?(?:current )?(?:time|date)\b", low)
    if m or re.match(r"what time is it|what(?:'s| is) today'?s date", low):
        import datetime
        return Intent("say", text=datetime.datetime.now().strftime("It is %A, %d %B %Y, %H:%M."))

    # conditional: "if chrome isn't running, open it"
    m = re.match(r"if\s+(.+?)\s+(?:is not|isn'?t|is n't|not)\s+running,?\s*(?:then\s+)?(?:open|start|launch)\b.*", low)
    if m:
        return Intent("ensure_running", "open_application", {"name": _unquote(m.group(1))}, f"Ensure {m.group(1)} is running")

    # --- remember / recall -------------------------------------------------------------------------------
    m = re.match(r"(?:please\s+)?remember(?:\s+that)?\s+(.+)", c, re.I)
    if m:
        return Intent("call", "remember", {"content": m.group(1).strip()}, "Remember")
    m = re.match(r"(?:what did i (?:ask you to )?remember|what do you (?:remember|know)|recall)\s*(?:about|regarding)?\s*(.*)", c, re.I)
    if m:
        return Intent("call", "recall_memory", {"query": m.group(1).strip() or "everything"}, "Recall memory")
    m = re.match(r"(?:forget|delete memory)\s+(?:memory\s+)?#?(\d+)", c, re.I)
    if m:
        return Intent("call", "forget_memory", {"memory_id": int(m.group(1))}, "Forget memory")

    # --- network / news / map / system --------------------------------------------------------------------
    if re.search(r"\b(scan|discover|find|show|list)\b.*\b(network|devices|wifi|wi-fi)\b", low) and "file" not in low:
        return Intent("call", "scan_network", {}, "Scan the local network")
    m = re.search(r"\b(?:show|get|read|fetch|open)?\s*(?:me\s+)?(?:today'?s\s+|the\s+|latest\s+|live\s+)*(global|world|india|technology|tech|science|business|sports?|entertainment|security|weather)?\s*news\b", low)
    if m and not re.search(r"\bsearch\b", low):
        cat = {"global": "World", "tech": "Technology", "sport": "Sports"}.get((m.group(1) or "world"), (m.group(1) or "world").title())
        return Intent("call", "get_live_news", {"category": cat}, f"Fetch {cat} news")
    m = re.search(r"\b(?:show|open)\b.*\b(?:live\s+)?(?:world\s+)?map\b(?:\s+of\s+(.+))?", low)
    if m:
        return Intent("call", "show_map", {"place": m.group(1).strip()} if m.group(1) else {}, "Open the live map")
    if re.search(r"\b(cpu|ram|memory usage|system (?:status|stats|info)|battery|disk (?:usage|space))\b", low) and "remember" not in low:
        return Intent("call", "get_system_stats", {}, "Read system stats")
    if re.search(r"\bscreenshot\b|\bscreen ?shot\b", low):
        return Intent("call", "screenshot", {}, "Take a screenshot")

    # --- youtube / web -------------------------------------------------------------------------------------
    m = re.match(r"(?:please\s+)?play\s+(?:the\s+)?(?:(" + ORD + r")\s+(?:one|video|result)$|(.+?))(?:\s+on\s+youtube)?$", c, re.I)
    if m:
        if m.group(1):
            item = ctx.pick(m.group(1))
            if item and item.get("video_id"):
                return Intent("call", "play_youtube", {"video_id": item["video_id"]}, f"Play: {item.get('title', '')}")
            return Intent("say", text="I don't have a list of videos to pick from yet. Ask me to search YouTube first.")
        return Intent("call", "play_youtube", {"query": _unquote(m.group(2))}, f"Play '{_unquote(m.group(2))}' on YouTube")
    m = re.match(r"(?:search|find|look up|look for)\s+(?:on\s+)?youtube\s+(?:for\s+)?(.+)", c, re.I) or \
        re.match(r"(?:search|find)\s+(.+?)\s+on\s+youtube$", c, re.I)
    if m:
        return Intent("call", "youtube_search", {"query": _unquote(m.group(1))}, "Search YouTube")
    m = re.match(r"(?:search|google|look up)\s+(?:the\s+web\s+|google\s+|online\s+)?(?:for\s+)?(.+)", c, re.I)
    if m and not re.search(r"\b(files?|folders?)\b", low):
        return Intent("call", "search_web", {"query": _unquote(m.group(1))}, "Search the web")
    m = re.match(r"(?:go to|navigate to|browse to|visit|open)\s+(https?://\S+|" + URL_RE.pattern + ")$", c, re.I)
    if m:
        return Intent("call", "navigate", {"url": m.group(1)}, f"Open {m.group(1)}")
    m = re.match(rf"open\s+(?:the\s+)?({ORD})\s*(?:one|result|link)?$", c, re.I)
    if m:
        item = ctx.pick(m.group(1))
        if item and item.get("href"):
            return Intent("call", "navigate", {"url": item["href"]}, "Open result")
        if item and item.get("link"):
            return Intent("call", "navigate", {"url": item["link"]}, "Open article")
        if item and item.get("path"):
            return Intent("call", "open_path", {"path": item["path"]}, "Open item")
        return Intent("say", text="There is no numbered list to pick from yet.")
    m = re.match(r"download\s+(?:the\s+)?(?:file\s+)?(?:from\s+)?(https?://\S+)", c, re.I)
    if m:
        return Intent("call", "download_file", {"url": m.group(1)}, "Download file")
    if re.match(rf"download\s+{PRON}$", c, re.I) and ctx.last_url:
        return Intent("call", "download_file", {"url": ctx.last_url}, "Download file")

    # --- files ------------------------------------------------------------------------------------------------
    m = re.match(r"(?:create|make|new)\s+(?:a\s+|an\s+|new\s+)*(?:folder|directory)\s*(.*)", c, re.I)
    if m:
        name, loc = _name_and_loc(m.group(1))
        if not name:
            return Intent("say", text="What should the folder be called?")
        return Intent("call", "create_folder", {"path": f"{loc}/{name}" if loc else name}, f"Create folder {name}")
    c = re.sub(r"\s+(?:inside|in)\s+(?:it|that|there|the same folder)\b", " INSIDE_CTX", c, flags=re.I)
    m = re.match(r"(?:create|make|new)\s+(?:a\s+|an\s+|new\s+)*file\s*(.*?)(?:\s+(?:with|containing)\s+(?:content|text)?\s*[:\-]?\s*(.+))?$", c, re.I)
    if m:
        name, loc = _name_and_loc(m.group(1))
        if not name:
            return Intent("say", text="What should the file be called?")
        if "INSIDE_CTX" in name or "INSIDE_CTX" in c:
            name = name.replace("INSIDE_CTX", "").strip()
            path = f"{ctx.last_folder}/{name}" if ctx.last_folder else name
        else:
            path = f"{loc}/{name}" if loc else name
        return Intent("call", "create_file", {"path": path, "content": _unquote(m.group(2) or "")}, f"Create file {name}")
    m = re.match(r"(?:delete|remove|trash)\s+(.+)", c, re.I)
    if m:
        p, why = _target(m.group(1), ctx)
        if not p:
            return Intent("say", text="I don't know which file or folder you mean.")
        is_folder = bool(re.search(r"\bfolder|directory\b", m.group(1), re.I)) or (p == ctx.last_folder and not ctx.last_path == p and False)
        return Intent("call", "delete_folder" if is_folder else "delete_file", {"path": p}, f"Delete {why}")
    m = re.match(r"rename\s+(.+?)\s+to\s+(.+)", c, re.I)
    if m:
        p, why = _target(m.group(1), ctx)
        if not p:
            return Intent("say", text="I don't know which file you mean.")
        return Intent("call", "rename_file", {"path": p, "new_name": _unquote(m.group(2))}, f"Rename {why}")
    m = re.match(r"(move|copy)\s+(.+?)\s+(?:to|into|in)\s+(.+)", c, re.I)
    if m:
        s, why = _target(m.group(2), ctx)
        d, dwhy = _target(m.group(3), ctx)
        if not s or not d:
            return Intent("say", text=f"I couldn't work out what to {m.group(1).lower()} and where.")
        return Intent("call", "move_file" if m.group(1).lower() == "move" else "copy_file", {"src": s, "dst": d}, f"{m.group(1).title()} {why} → {dwhy}")
    m = re.match(r"(?:list|show)\s+(?:me\s+)?(?:the\s+)?(?:files|contents|folders?)?\s*(?:in|of|on|inside)\s+(.+)", c, re.I)
    if m:
        p, _ = _target(m.group(1), ctx)
        return Intent("call", "list_directory", {"path": p or "~"}, "List folder")
    m = re.match(r"(?:find|search(?: for)?|locate)\s+(?:files?\s+)?(?:named\s+|called\s+)?(.+?)(?:\s+(?:in|on)\s+(?:my\s+)?(desktop|documents|downloads))?$", c, re.I)
    if m and re.search(r"\bfiles?\b|\bfolders?\b|\.\w{2,4}\b", low):
        return Intent("call", "search_files", {"query": _unquote(re.sub(r"\b(files?|folders?)\b", "", m.group(1), flags=re.I).strip()),
                                               "root": m.group(2) or "~"}, "Search files")
    m = re.match(rf"open\s+(?:the\s+)?(?:file|folder|directory)?\s*({PRON}|.*[\\/].*|~.*|.+\.\w{{2,5}})$", c, re.I)
    if m and not URL_RE.fullmatch(m.group(1).strip()):
        p, _ = _target(m.group(1), ctx)
        if p:
            return Intent("call", "open_path", {"path": p}, "Open")
    m = re.match(r"open\s+(?:the\s+)?(.+?)\s+(?:folder|directory)$", c, re.I)
    if m:
        p, _ = _target(m.group(1) + " folder", ctx)
        return Intent("call", "open_path", {"path": p or m.group(1)}, "Open folder")
    m = re.match(r"read\s+(.+)", c, re.I)
    if m:
        p, _ = _target(m.group(1), ctx)
        if p:
            return Intent("call", "read_file", {"path": p}, "Read file")

    # --- volume ----------------------------------------------------------------------------------------------
    m = re.fullmatch(r"(?:please\s+)?(?:set|change|make|put)?\s*(?:the\s+)?(?:system\s+)?(?:volume|sound)\s*(?:level\s*)?(?:to|at)?\s*(\d{1,3})\s*(?:%|percent)?", low) or \
        re.fullmatch(r"(?:please\s+)?(?:set|change|make|put)\s+(?:the\s+)?(?:volume|sound)\s+(?:to\s+)?(\d{1,3})\s*(?:%|percent)?", low)
    if m:
        return Intent("call", "set_volume", {"percent": min(100, int(m.group(1)))}, f"Set volume to {min(100, int(m.group(1)))}%")
    m = re.fullmatch(r"(?:please\s+)?(?:(increase|raise|turn up|lower|decrease|reduce|turn down)\s+(?:the\s+)?(?:volume|sound)|volume\s+(up|down)|(louder|quieter))(?:\s+by\s+(\d{1,2})\s*(?:%|percent)?)?", low)
    if m:
        up = (m.group(1) or m.group(2) or m.group(3) or "") in ("increase", "raise", "turn up", "up", "louder")
        step = int(m.group(4) or 10)
        return Intent("call", "set_volume", {"delta": step if up else -step}, f"Volume {'up' if up else 'down'} {step}%")
    m = re.fullmatch(r"(?:please\s+)?(mute|unmute)(?:\s+(?:the\s+)?(?:sound|volume|audio|speakers?|system|computer))?", low)
    if m:
        return Intent("call", "set_volume", {"mute": m.group(1) == "mute"}, "Mute" if m.group(1) == "mute" else "Unmute")
    if re.fullmatch(r"(?:what(?:'s| is) the |how loud is the |check the |show the )?(?:current )?(?:volume|sound level)(?: level)?[?. ]*", low):
        return Intent("call", "get_volume", {}, "Read volume")

    # --- applications (last: the most generic 'open X') -------------------------------------------------------
    m = re.match(r"(?:please\s+)?(?:open|launch|start|run|khol(?:o)?|chalao)\s+(?:the\s+|my\s+)?(.+?)(?:\s+(?:app|application|program))?$", c, re.I)
    if m:
        name = _unquote(m.group(1))
        if re.fullmatch(PRON, name, re.I) and ctx.last_app:
            name = ctx.last_app
        site = SITES.get(re.sub(r"\.com$", "", name.lower()).strip())
        if site:    # "open YouTube" means the website, not a program called YouTube
            return Intent("call", "open_url", {"url": site}, f"Open {name} in your browser")
        return Intent("call", "open_application", {"name": name}, f"Open {name}")
    m = re.match(r"(?:close|quit|exit|kill|band karo|band kar)\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|program))?$", c, re.I)
    if m:
        name = _unquote(m.group(1))
        if re.fullmatch(PRON, name, re.I) and ctx.last_app:
            name = ctx.last_app
        return Intent("call", "close_application", {"name": name}, f"Close {name}")
    return None


def expand(it: Intent) -> list[Intent]:
    """'open Chrome and File Explorer' → two independent intents."""
    if it.kind == "call" and it.tool in ("open_application", "close_application") and re.search(r"\s+and\s+|,", it.args["name"]):
        verb = it.label.split(" ")[0]
        out = []
        for n in [x.strip() for x in re.split(r"\s+and\s+|,", it.args["name"]) if x.strip()]:
            site = SITES.get(re.sub(r"\.com$", "", n.lower())) if it.tool == "open_application" else None
            out.append(Intent("call", "open_url", {"url": site}, f"Open {n} in your browser") if site else Intent("call", it.tool, {"name": n}, f"{verb} {n}"))
        return out
    return [it]


def plan(text: str, ctx: Context) -> tuple[list[Intent], list[str]]:
    """Static plan against the *current* context (the engine plans clause-by-clause at run time so that
    'inside it' refers to what the previous step actually produced)."""
    intents, unmapped = [], []
    for clause in split_clauses(text):
        it = plan_clause(clause, ctx)
        if it is None:
            unmapped.append(clause)
            continue
        for e in expand(it):
            if not (intents and intents[-1].kind == e.kind == "call" and intents[-1].tool == e.tool and intents[-1].args == e.args):
                intents.append(e)
    return intents, unmapped
