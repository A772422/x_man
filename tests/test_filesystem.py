import zipfile

import pytest

from conftest import auto_confirm

from mrx.tools import filesystem as fs
from mrx.tools.registry import ToolError


async def run(rt, name, **a):
    return await rt.registry.execute(name, a)


async def test_create_read_modify_append(rt, home):
    r = await run(rt, "create_folder", path="Desktop/Project X")
    assert r.success and (home / "Desktop/Project X").is_dir()
    r = await run(rt, "create_file", path="Desktop/Project X/a.txt", content="hello")
    assert r.success and r.verified
    assert (await run(rt, "read_file", path="Desktop/Project X/a.txt")).result["content"] == "hello"
    assert (await run(rt, "modify_file", path="Desktop/Project X/a.txt", find="hello", replace="bye")).verified
    assert (await run(rt, "append_file", path="Desktop/Project X/a.txt", content="!!")).verified
    assert (home / "Desktop/Project X/a.txt").read_text() == "bye!!"
    assert not (await run(rt, "create_file", path="Desktop/Project X/a.txt", content="z")).success  # no silent overwrite


async def test_copy_move_rename(rt, home):
    (home / "Documents/f.txt").write_text("data")
    assert (await run(rt, "copy_file", src="Documents/f.txt", dst="Desktop")).verified
    assert (home / "Desktop/f.txt").read_text() == "data"
    auto_confirm(rt)
    r = await run(rt, "move_file", src="Desktop/f.txt", dst="Documents/g.txt")
    assert r.success and (home / "Documents/g.txt").exists() and not (home / "Desktop/f.txt").exists()
    r = await run(rt, "rename_file", path="Documents/g.txt", new_name="h.txt")
    assert r.success and (home / "Documents/h.txt").exists()
    assert not (await run(rt, "rename_file", path="Documents/h.txt", new_name="../x")).success


async def test_delete_goes_to_trash_and_verifies(rt, home):
    (home / "Desktop/x.txt").write_text("keep me")
    auto_confirm(rt)
    r = await run(rt, "delete_file", path="Desktop/x.txt")
    assert r.success and r.verified and not (home / "Desktop/x.txt").exists()
    trashed = list((home / ".mrx/trash").rglob("x.txt"))
    assert trashed and trashed[0].read_text() == "keep me"  # recoverable


async def test_outside_home_and_protected_folders_refused(rt, home):
    r = await run(rt, "read_file", path="/etc/passwd")
    assert not r.success and "outside" in r.error
    r = await run(rt, "create_file", path="/tmp/evil.txt", content="x")
    assert not r.success
    with pytest.raises(ToolError):
        fs._protected(rt, home)
    with pytest.raises(ToolError):
        fs._protected(rt, home / "Desktop")


async def test_search_and_list(rt, home):
    (home / "Documents/report-2026.md").write_text("quarterly numbers")
    (home / "Documents/other.txt").write_text("nothing")
    r = await run(rt, "search_files", query="report", root="Documents")
    assert [m["name"] for m in r.result["matches"]] == ["report-2026.md"]
    r = await run(rt, "search_files", query="quarterly", root="Documents", content=True)
    assert len(r.result["matches"]) == 1
    r = await run(rt, "list_directory", path="Documents")
    assert r.result["count"] == 2


async def test_archive_roundtrip_and_zip_slip(rt, home):
    (home / "Documents/a.txt").write_text("A")
    r = await run(rt, "archive_files", paths=["Documents/a.txt"], dest="Desktop/x.zip")
    assert r.success and r.verified
    r = await run(rt, "extract_archive", archive="Desktop/x.zip", dest="Desktop/out")
    assert r.success and (home / "Desktop/out/a.txt").read_text() == "A"
    with zipfile.ZipFile(home / "evil.zip", "w") as z:
        z.writestr("../../escaped.txt", "boom")
    r = await run(rt, "extract_archive", archive="evil.zip", dest="Desktop/out2")
    assert not r.success and "unsafe" in r.error and not (home.parent / "escaped.txt").exists()


async def test_unsupported_binary_read_is_honest(rt, home):
    (home / "x.bin").write_bytes(b"\x00\x01")
    r = await run(rt, "read_file", path="x.bin")
    assert not r.success and "cannot read" in r.error
