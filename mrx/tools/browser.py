"""Browser Agent on Playwright. Operates a real browser and a real page; every action reports what it
actually observed afterwards (URL, title, DOM change, file on disk)."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from .registry import Outcome, Tool, ToolError

SEARCH_ENGINES = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
}


def normalize_url(url: str) -> str:
    u = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.\-]*:", u, re.I):
        u = "https://" + u
    if urlparse(u).scheme not in ("http", "https", "file", "about"):
        raise ToolError(f"unsupported URL scheme in '{url}'")
    return u


class BrowserController:
    def __init__(self, rt):
        self.rt = rt
        self._pw = None
        self._browser = None
        self.ctx = None
        self.active = None
        self._lock = asyncio.Lock()
        self.dialogs: list[dict] = []
        self.downloads: list[dict] = []

    @property
    def running(self) -> bool:
        return self.ctx is not None

    def _emit(self, type_: str, data: dict) -> None:
        asyncio.ensure_future(self.rt.bus.emit(type_, data))

    async def ensure(self):
        async with self._lock:
            if self.ctx is not None:
                try:
                    _ = self.ctx.pages  # touch: raises if the browser was closed by the user
                    if self._browser is None or self._browser.is_connected():
                        return self.ctx
                except Exception:
                    pass
                await self._teardown()
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise ToolError("Playwright is not installed. Run: pip install playwright && playwright install chromium")
            cfg = self.rt.settings.get("browser", {})
            self._pw = await async_playwright().start()
            engine = getattr(self._pw, cfg.get("engine", "chromium"))
            try:
                if cfg.get("cdp_url"):
                    self._browser = await engine.connect_over_cdp(cfg["cdp_url"])
                    self.ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
                elif cfg.get("persistent_profile", True):
                    self.ctx = await engine.launch_persistent_context(
                        str(self.rt.settings.home / "browser-profile"), headless=cfg.get("headless", False),
                        channel=cfg.get("channel") or None, accept_downloads=True,
                        args=["--no-sandbox"] if _is_root() else [])
                else:
                    self._browser = await engine.launch(headless=cfg.get("headless", False),
                                                        channel=cfg.get("channel") or None,
                                                        args=["--no-sandbox"] if _is_root() else [])
                    self.ctx = await self._browser.new_context(accept_downloads=True)
            except Exception as e:
                await self._teardown()
                raise ToolError(f"could not launch browser: {str(e).splitlines()[0]}. "
                                f"If browsers are missing run: playwright install chromium")
            self.ctx.on("page", self._on_page)
            for p in self.ctx.pages:
                self._on_page(p)
            self.active = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
            await self.rt.bus.emit("browser.started", {"headless": cfg.get("headless", False)})
            return self.ctx

    def _on_page(self, page) -> None:
        self.active = page
        page.on("framenavigated", lambda f: f == page.main_frame and self._emit(
            "browser.navigation", {"url": f.url}))
        page.on("dialog", lambda d: asyncio.ensure_future(self._dialog(d)))
        page.on("download", lambda d: asyncio.ensure_future(self._download(d)))
        page.on("close", lambda _: self._page_closed(page))

    def _page_closed(self, page) -> None:
        if self.active is page and self.ctx:
            pages = [p for p in self.ctx.pages if not p.is_closed()]
            self.active = pages[-1] if pages else None

    async def _dialog(self, d) -> None:
        self.dialogs.append({"type": d.type, "message": d.message, "ts": time.time()})
        self.dialogs = self.dialogs[-20:]
        await self.rt.bus.emit("browser.dialog", {"type": d.type, "message": d.message})
        try:
            await d.accept()
        except Exception:
            pass

    async def _download(self, d) -> None:
        dest = self.rt.settings.downloads_dir / (Path(d.suggested_filename).name or "download")
        try:
            await d.save_as(str(dest))
            info = {"path": str(dest), "url": d.url, "size": dest.stat().st_size}
            self.downloads.append(info)
            await self.rt.bus.emit("browser.download", info)
        except Exception as e:
            await self.rt.bus.emit("browser.download", {"error": str(e), "url": d.url})

    async def page(self):
        await self.ensure()
        if self.active is None or self.active.is_closed():
            self.active = await self.ctx.new_page()
        return self.active

    async def _teardown(self):
        for c in (self.ctx, self._browser):
            try:
                if c:
                    await c.close()
            except Exception:
                pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self.ctx = self._browser = self._pw = self.active = None

    async def close(self):
        async with self._lock:
            await self._teardown()

    async def state(self) -> dict:
        if not self.running:
            return {"running": False, "tabs": []}
        tabs = []
        for i, p in enumerate(self.ctx.pages):
            try:
                tabs.append({"index": i, "url": p.url, "title": await p.title(), "active": p is self.active})
            except Exception:
                tabs.append({"index": i, "url": p.url, "title": "", "active": p is self.active})
        return {"running": True, "tabs": tabs, "last_dialogs": self.dialogs[-3:], "downloads": self.downloads[-5:]}


def _is_root() -> bool:
    import os
    return hasattr(os, "geteuid") and os.geteuid() == 0


async def _digest(page) -> str:
    try:
        return hashlib.md5((page.url + await page.evaluate("document.body ? document.body.innerText.length + '|' + document.body.innerHTML.length : ''")).encode()).hexdigest()
    except Exception:
        return page.url


async def open_browser(rt, url: str | None = None):
    await rt.browser.ensure()
    if url:
        return await navigate(rt, url)
    st = await rt.browser.state()
    return Outcome(st, st["running"], "browser context is alive")


async def close_browser(rt):
    was = rt.browser.running
    await rt.browser.close()
    return Outcome({"was_running": was}, not rt.browser.running, "browser context closed")


async def list_tabs(rt):
    await rt.browser.ensure()
    return await rt.browser.state()


async def new_tab(rt, url: str | None = None):
    await rt.browser.ensure()
    before = len(rt.browser.ctx.pages)
    page = await rt.browser.ctx.new_page()
    rt.browser.active = page
    if url:
        return await navigate(rt, url)
    ok = len(rt.browser.ctx.pages) == before + 1
    return Outcome({"index": rt.browser.ctx.pages.index(page)}, ok, f"tab count {before}→{len(rt.browser.ctx.pages)}")


async def close_tab(rt, index: int | None = None):
    await rt.browser.ensure()
    pages = rt.browser.ctx.pages
    page = pages[index] if index is not None and 0 <= index < len(pages) else rt.browser.active
    if page is None:
        raise ToolError("no tab to close")
    before = len(pages)
    await page.close()
    ok = len([p for p in rt.browser.ctx.pages if not p.is_closed()]) == before - 1
    return Outcome({"tabs_left": len(rt.browser.ctx.pages)}, ok, f"tab count {before}→{len(rt.browser.ctx.pages)}")


async def switch_tab(rt, index: int | None = None, title: str | None = None):
    await rt.browser.ensure()
    pages = rt.browser.ctx.pages
    target = None
    if index is not None and 0 <= index < len(pages):
        target = pages[index]
    elif title:
        for p in pages:
            if title.lower() in (await p.title()).lower() or title.lower() in p.url.lower():
                target = p
                break
    if target is None:
        raise ToolError("no matching tab")
    await target.bring_to_front()
    rt.browser.active = target
    return Outcome({"url": target.url, "title": await target.title()}, rt.browser.active is target, "tab is now active")


async def navigate(rt, url: str, wait_until: str = "domcontentloaded"):
    u = normalize_url(url)
    page = await rt.browser.page()
    try:
        resp = await page.goto(u, wait_until=wait_until, timeout=30000)
    except Exception as e:
        raise ToolError(f"navigation to {u} failed: {str(e).splitlines()[0]}", transient=True)
    status = resp.status if resp else None
    title = await page.title()
    host_ok = urlparse(u).scheme in ("file", "about") or (urlparse(page.url).hostname or "").split(".")[-2:] == (urlparse(u).hostname or "").split(".")[-2:]
    ok = (status is None or status < 400) and (host_ok or bool(title))
    return Outcome({"url": page.url, "title": title, "status": status}, ok,
                   f"loaded {page.url} (HTTP {status}), title '{title}'")


async def search_web(rt, query: str, engine: str = "duckduckgo"):
    if engine not in SEARCH_ENGINES:
        raise ToolError(f"engine must be one of {list(SEARCH_ENGINES)}")
    res = await navigate(rt, SEARCH_ENGINES[engine].format(q=quote_plus(query)))
    page = await rt.browser.page()
    try:
        await page.wait_for_load_state("load", timeout=8000)
    except Exception:
        pass
    links = await _links(page, 10)
    ok = bool(res.verified) and quote_plus(query).split("+")[0].lower() in page.url.lower()
    return Outcome({"url": page.url, "engine": engine, "results": links}, ok,
                   f"search page loaded with {len(links)} links extracted")


async def _links(page, limit: int, contains: str = "") -> list[dict]:
    items = await page.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a => ({text:(a.innerText||a.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,160), href:a.href}))
        .filter(x => x.text && /^https?:/.test(x.href))""")
    seen, out = set(), []
    for it in items:
        if it["href"] in seen or (contains and contains.lower() not in (it["href"] + it["text"]).lower()):
            continue
        seen.add(it["href"])
        out.append({"index": len(out) + 1, **it})
        if len(out) >= limit:
            break
    return out


async def extract_links(rt, limit: int = 30, contains: str = ""):
    page = await rt.browser.page()
    return {"url": page.url, "links": await _links(page, limit, contains)}


async def read_page(rt, max_chars: int = 8000):
    page = await rt.browser.page()
    text = await page.evaluate("document.body ? document.body.innerText : ''")
    return {"url": page.url, "title": await page.title(), "text": text[:max_chars], "chars": len(text),
            "truncated": len(text) > max_chars}


async def extract_structured(rt, selector: str, attributes: list | None = None, limit: int = 50):
    page = await rt.browser.page()
    rows = await page.evaluate("""([sel, attrs, limit]) => [...document.querySelectorAll(sel)].slice(0, limit).map(e => {
        const o = {text: (e.innerText||'').trim().slice(0,300)}; for (const a of attrs) o[a] = e.getAttribute(a); return o; })""",
                               [selector, attributes or ["href"], limit])
    return {"selector": selector, "count": len(rows), "items": rows}


async def _locators(page, selector, text, role, name, label, placeholder):
    """Ordered strategies: the recovery ladder used when one locator fails."""
    strategies = []
    if selector:
        strategies.append(("css/selector", page.locator(selector).first))
    if role:
        strategies.append(("role+name", page.get_by_role(role, name=name or text).first))
    if label:
        strategies.append(("label", page.get_by_label(label).first))
    if placeholder:
        strategies.append(("placeholder", page.get_by_placeholder(placeholder).first))
    t = text or name
    if t:
        strategies += [("accessible name (button)", page.get_by_role("button", name=t).first),
                       ("accessible name (link)", page.get_by_role("link", name=t).first),
                       ("visible text", page.get_by_text(t, exact=False).first)]
    if not strategies:
        raise ToolError("provide selector, text, role/name, label or placeholder")
    return strategies


async def click_element(rt, selector: str | None = None, text: str | None = None, role: str | None = None,
                        name: str | None = None, label: str | None = None, double: bool = False):
    page = await rt.browser.page()
    before = await _digest(page)
    errors = []
    for how, loc in await _locators(page, selector, text, role, name, label, None):
        try:
            await (loc.dblclick(timeout=3000) if double else loc.click(timeout=3000))
            await asyncio.sleep(0.6)
            changed = (await _digest(page)) != before
            return Outcome({"strategy": how, "url": page.url, "page_changed": changed}, None,
                           f"clicked via {how}; page {'changed' if changed else 'did not visibly change'}")
        except Exception as e:
            errors.append(f"{how}: {str(e).splitlines()[0][:80]}")
    raise ToolError("element not found/clickable after trying " + "; ".join(errors))


async def type_text(rt, text: str, selector: str | None = None, label: str | None = None,
                    placeholder: str | None = None, submit: bool = False, clear: bool = True):
    page = await rt.browser.page()
    errors = []
    for how, loc in await _locators(page, selector, None, None, None, label, placeholder) if (selector or label or placeholder) else [("focused element", page.locator(":focus"))]:
        try:
            if clear:
                await loc.fill(text, timeout=3000)
            else:
                await loc.type(text, timeout=3000)
            value = await loc.input_value(timeout=1500)
            ok = text in value if clear else True
            if submit:
                await loc.press("Enter")
                await asyncio.sleep(0.8)
            return Outcome({"strategy": how, "url": page.url}, ok, f"field value read back: {value[:60]!r}")
        except Exception as e:
            errors.append(f"{how}: {str(e).splitlines()[0][:80]}")
    raise ToolError("could not type into the field: " + "; ".join(errors))


async def fill_form(rt, fields: list):
    results = []
    for f in fields:
        r = await type_text(rt, f["value"], selector=f.get("selector"), label=f.get("label"), placeholder=f.get("placeholder"))
        results.append({"field": f.get("selector") or f.get("label") or f.get("placeholder"), "verified": r.verified})
    return Outcome({"fields": results}, all(x["verified"] for x in results), f"{sum(bool(x['verified']) for x in results)}/{len(results)} fields read back correctly")


async def scroll_page(rt, direction: str = "down", amount: int = 600):
    page = await rt.browser.page()
    before = await page.evaluate("window.scrollY")
    await page.evaluate("([d,a]) => window.scrollBy(0, d==='up'? -a : a)", [direction, amount])
    await asyncio.sleep(0.2)
    after = await page.evaluate("window.scrollY")
    return Outcome({"scroll_y": after}, after != before or direction in ("up", "down"), f"scrollY {before}→{after}")


async def wait_for(rt, selector: str | None = None, text: str | None = None, timeout_s: float = 10):
    page = await rt.browser.page()
    try:
        if selector:
            await page.wait_for_selector(selector, timeout=timeout_s * 1000)
        elif text:
            await page.get_by_text(text).first.wait_for(timeout=timeout_s * 1000)
        else:
            raise ToolError("provide selector or text")
    except ToolError:
        raise
    except Exception:
        raise ToolError("timed out waiting")
    return Outcome({"found": True}, True, "condition met")


async def run_javascript(rt, script: str):
    page = await rt.browser.page()
    try:
        return {"result": await page.evaluate(script)}
    except Exception as e:
        raise ToolError(f"script error: {str(e).splitlines()[0]}")


async def browser_screenshot(rt, full_page: bool = False):
    page = await rt.browser.page()
    d = rt.settings.home / "screenshots"
    d.mkdir(exist_ok=True)
    path = d / f"browser-{time.strftime('%Y%m%d-%H%M%S')}.png"
    await page.screenshot(path=str(path), full_page=full_page)
    ok = path.is_file() and path.stat().st_size > 0
    return Outcome({"path": str(path), "url": page.url}, ok, f"{path.stat().st_size if ok else 0} bytes")


async def download_file(rt, url: str | None = None, selector: str | None = None, filename: str | None = None):
    await rt.browser.ensure()
    dest_dir: Path = rt.settings.downloads_dir
    if selector:
        page = await rt.browser.page()
        async with page.expect_download(timeout=30000) as dl:
            await page.locator(selector).first.click()
        d = await dl.value
        dest = dest_dir / (Path(filename).name if filename else Path(d.suggested_filename).name or "download")
        await d.save_as(str(dest))
    elif url:
        u = normalize_url(url)
        if urlparse(u).scheme not in ("http", "https"):
            raise ToolError("downloads must be http(s) URLs")
        resp = await rt.browser.ctx.request.get(u, timeout=60000)
        if not resp.ok:
            raise ToolError(f"download failed: HTTP {resp.status}")
        name = Path(filename).name if filename else (Path(urlparse(u).path).name or "download")
        dest = dest_dir / name
        dest.write_bytes(await resp.body())
    else:
        raise ToolError("provide url or selector")
    size = dest.stat().st_size if dest.exists() else 0
    return Outcome({"path": str(dest), "bytes": size}, size > 0, f"file on disk: {size} bytes")


async def upload_file(rt, selector: str, path: str):
    from .filesystem import resolve_path
    p = resolve_path(rt, path)
    if not p.is_file():
        raise ToolError(f"'{p}' is not a file")
    page = await rt.browser.page()
    await page.locator(selector).first.set_input_files(str(p))
    n = await page.locator(selector).first.evaluate("e => e.files ? e.files.length : 0")
    return Outcome({"path": str(p)}, n == 1, f"input now holds {n} file(s)")


def tools() -> list[Tool]:
    S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
    K = "plugin:browser"
    return [
        Tool("open_browser", "Launch the automation browser (optionally at a URL).", {"url": S}, [], open_browser, plugin="browser", scope=K),
        Tool("close_browser", "Close the automation browser.", {}, [], close_browser, plugin="browser", scope=K),
        Tool("list_tabs", "List open tabs.", {}, [], list_tabs, plugin="browser", scope=K),
        Tool("new_tab", "Open a new tab, optionally at a URL.", {"url": S}, [], new_tab, plugin="browser", scope=K),
        Tool("close_tab", "Close a tab (by index, default current).", {"index": I}, [], close_tab, plugin="browser", scope=K),
        Tool("switch_tab", "Switch to a tab by index or title/URL text.", {"index": I, "title": S}, [], switch_tab, plugin="browser", scope=K),
        Tool("navigate", "Go to a URL in the current tab. Verified by final URL, status and title.", {"url": S}, ["url"], navigate, retryable=True, plugin="browser", scope=K),
        Tool("search_web", "Search the web in the browser and return result links.", {"query": S, "engine": {"type": "string", "enum": list(SEARCH_ENGINES)}}, ["query"], search_web, plugin="browser", scope=K),
        Tool("extract_links", "List links on the current page.", {"limit": I, "contains": S}, [], extract_links, plugin="browser", scope=K),
        Tool("read_page", "Read the visible text of the current page.", {"max_chars": I}, [], read_page, plugin="browser", scope=K),
        Tool("extract_structured", "Extract text/attributes of elements matching a CSS selector.", {"selector": S, "attributes": {"type": "array", "items": S}, "limit": I}, ["selector"], extract_structured, plugin="browser", scope=K),
        Tool("click_element", "Click an element; tries selector, role/name, label and visible text in turn.", {"selector": S, "text": S, "role": S, "name": S, "label": S, "double": B}, [], click_element, plugin="browser", scope=K),
        Tool("type_text", "Type into a form field (selector, label or placeholder).", {"text": S, "selector": S, "label": S, "placeholder": S, "submit": B, "clear": B}, ["text"], type_text, plugin="browser", scope=K),
        Tool("fill_form", "Fill several fields: [{selector|label|placeholder, value}].", {"fields": {"type": "array", "items": {"type": "object"}}}, ["fields"], fill_form, plugin="browser", scope=K),
        Tool("scroll_page", "Scroll the page.", {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": I}, [], scroll_page, plugin="browser", scope=K),
        Tool("wait_for", "Wait for a selector or text to appear.", {"selector": S, "text": S, "timeout_s": {"type": "number"}}, [], wait_for, plugin="browser", scope=K),
        Tool("run_javascript", "Execute JavaScript in the current page.", {"script": S}, ["script"], run_javascript, risk=2, plugin="browser", scope=K),
        Tool("browser_screenshot", "Screenshot the current page.", {"full_page": B}, [], browser_screenshot, plugin="browser", scope=K),
        Tool("download_file", "Download a file by URL or by clicking a selector; saved to the download folder.", {"url": S, "selector": S, "filename": S}, [], download_file, retryable=True, plugin="browser", scope=K),
        Tool("upload_file", "Attach a local file to a file input.", {"selector": S, "path": S}, ["selector", "path"], upload_file, risk=2, plugin="browser", scope=K),
    ]
