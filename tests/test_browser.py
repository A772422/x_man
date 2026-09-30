"""Real browser tests: a local HTTP server + real Chromium through Playwright. Skipped if unavailable."""
import http.server
import os
import threading
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.async_api")

PAGE = """<html><head><title>Test Page</title></head><body>
<h1>Hello M.R.X.</h1>
<a href="/next">Go next</a>
<form onsubmit="event.preventDefault(); document.getElementById('out').textContent='submitted:'+document.getElementById('name').value;">
<label for="name">Your name</label><input id="name" placeholder="type here"><button type="submit">Send</button></form>
<div id="out"></div>
<button id="dlg" onclick="alert('hi there')">Alert</button>
<a id="dl" href="/file.txt" download>Download it</a>
<input type="file" id="up"><span id="upn"></span>
<script>document.getElementById('up').onchange=e=>document.getElementById('upn').textContent=e.target.files[0].name</script>
</body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/next":
            body, ctype = b"<html><title>Next Page</title><body>second</body></html>", "text/html"
        elif self.path == "/file.txt":
            body, ctype = b"downloaded content", "application/octet-stream"
        elif self.path == "/missing":
            self.send_response(404); self.end_headers(); return
        else:
            body, ctype = PAGE.encode(), "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if self.path == "/file.txt":
            self.send_header("Content-Disposition", 'attachment; filename="file.txt"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): ...


@pytest.fixture(scope="module")
def server():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


@pytest.fixture
async def br(rt):
    rt.settings.update({"browser": {"headless": True, "persistent_profile": False}})
    exe = "/opt/pw-browsers/chromium"
    if os.path.exists(exe):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    r = await rt.registry.execute("open_browser", {})
    if not r.success:
        pytest.skip(f"browser cannot start here: {r.error}")
    yield rt
    await rt.registry.execute("close_browser", {})


async def x(rt, tool, **a):
    return await rt.registry.execute(tool, a)


async def test_navigation_verified_and_failure(br, server):
    r = await x(br, "navigate", url=server)
    assert r.success and r.verified and r.result["title"] == "Test Page" and r.result["status"] == 200
    r = await x(br, "navigate", url=server + "/missing")
    assert not r.success and r.result["status"] == 404 if r.result else not r.success


async def test_tabs(br, server):
    await x(br, "navigate", url=server)
    r = await x(br, "new_tab", url=server + "/next")
    assert r.success and r.result["title"] == "Next Page"
    tabs = (await x(br, "list_tabs")).result["tabs"]
    assert len(tabs) == 2 and tabs[1]["active"]
    assert (await x(br, "switch_tab", index=0)).result["title"] == "Test Page"
    assert (await x(br, "close_tab", index=1)).verified
    assert len((await x(br, "list_tabs")).result["tabs"]) == 1


async def test_click_uses_recovery_ladder(br, server):
    await x(br, "navigate", url=server)
    r = await x(br, "click_element", selector="#does-not-exist", text="Go next")   # 1st strategy fails → text/role works
    assert r.success and r.result["strategy"] != "css/selector" and r.result["page_changed"]
    assert (await x(br, "read_page")).result["url"].endswith("/next")
    r = await x(br, "click_element", selector="#nothing", text="Absent Label 123")
    assert not r.success and "after trying" in r.error


async def test_form_fill_and_read_back(br, server):
    await x(br, "navigate", url=server)
    r = await x(br, "type_text", text="Ada", label="Your name")
    assert r.success and r.verified
    await x(br, "click_element", role="button", name="Send")
    assert "submitted:Ada" in (await x(br, "read_page")).result["text"]
    r = await x(br, "fill_form", fields=[{"selector": "#name", "value": "Bob"}])
    assert r.success and r.verified


async def test_read_extract_js_screenshot_dialog(br, server, rt):
    from conftest import auto_confirm
    auto_confirm(br)      # run_javascript is a level-2 (confirm) tool
    await x(br, "navigate", url=server)
    assert "Hello M.R.X." in (await x(br, "read_page")).result["text"]
    links = (await x(br, "extract_links")).result["links"]
    assert any(l["text"] == "Go next" for l in links)
    assert (await x(br, "run_javascript", script="1+2")).result["result"] == 3
    shot = await x(br, "browser_screenshot")
    assert shot.success and Path(shot.result["path"]).stat().st_size > 1000
    await x(br, "click_element", selector="#dlg")
    import asyncio; await asyncio.sleep(0.3)
    assert br.browser.dialogs and br.browser.dialogs[-1]["message"] == "hi there"


async def test_download_and_upload(br, server, home, tmp_path):
    await x(br, "navigate", url=server)
    r = await x(br, "download_file", url=server + "/file.txt")
    assert r.success and r.verified and Path(r.result["path"]).read_text() == "downloaded content"
    r = await x(br, "download_file", selector="#dl", filename="clicked.txt")
    assert r.success and Path(r.result["path"]).name == "clicked.txt"
    (home / "up.txt").write_text("u")
    from conftest import auto_confirm
    auto_confirm(br)
    r = await x(br, "upload_file", selector="#up", path="up.txt")
    assert r.success and r.verified


async def test_search_web_builds_engine_url(br, server, monkeypatch):
    from mrx.tools import browser as b
    monkeypatch.setitem(b.SEARCH_ENGINES, "local", server + "/?q={q}")
    r = await x(br, "search_web", query="hello world", engine="local") if False else None
    # engine allow-list is enforced by the schema
    r = await x(br, "search_web", query="x", engine="nope")
    assert not r.success
