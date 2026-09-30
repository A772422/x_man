import sys

import pytest

from mrx.tools import system as sysm


async def test_system_stats_shape_and_honest_gpu(rt):
    r = await rt.registry.execute("get_system_stats", {})
    d = r.result
    assert {"cpu_percent", "ram", "disk", "net", "uptime_s", "top_processes"} <= set(d)
    assert d["gpu"] is None or "util_percent" in d["gpu"]   # None when there is no NVIDIA GPU: never faked
    assert d["uptime_s"] > 0


async def test_process_listing_and_running_check(rt):
    r = await rt.registry.execute("list_processes", {"limit": 5})
    assert r.success and len(r.result["processes"]) <= 5
    r = await rt.registry.execute("is_application_running", {"name": "python"})
    assert r.result["running"] is True
    r = await rt.registry.execute("is_application_running", {"name": "zzz-nothing"})
    assert r.result["running"] is False


async def test_open_unknown_app_fails_with_reason(rt):
    from conftest import auto_confirm
    auto_confirm(rt)
    r = await rt.registry.execute("open_application", {"name": "zzz-nothing-installed", "wait_s": 1})
    assert not r.success and r.error


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses sleep")
async def test_open_and_close_real_process_verified(rt, tmp_path, monkeypatch):
    import os, stat
    exe = tmp_path / "mrxdemoapp"
    exe.write_text("#!/bin/sh\nexec sleep 60\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setitem(sysm.APPS, "mrxdemoapp", {"win": ["mrxdemoapp"], "mac": ["mrxdemoapp"], "linux": ["mrxdemoapp"], "proc": ["sleep"]})
    from conftest import auto_confirm
    auto_confirm(rt)   # a custom (non-catalogue) app needs confirmation
    r = await rt.registry.execute("open_application", {"name": "mrxdemoapp", "wait_s": 5})
    assert r.success and r.verified and r.result["pids"]
    from conftest import auto_confirm
    auto_confirm(rt)
    pid = r.result["pids"][0]
    r = await rt.registry.execute("kill_process", {"pid": pid, "force": True})
    assert r.success and r.verified


async def test_desktop_input_unavailable_is_reported(rt):
    r = await rt.registry.execute("mouse_position", {})
    if not r.success:                       # headless CI: must say so, not pretend
        assert r.status == "unavailable" and "desktop input is unavailable" in r.error


async def test_cannot_kill_self(rt):
    import os
    from conftest import auto_confirm
    auto_confirm(rt)
    r = await rt.registry.execute("kill_process", {"pid": os.getpid()})
    assert not r.success and "itself" in r.error


async def test_screenshot_reports_failure_or_real_file(rt):
    r = await rt.registry.execute("screenshot", {})
    if r.success:
        assert r.verified
    else:
        assert r.error
