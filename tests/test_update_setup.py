import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import set_key  # noqa: E402
import update  # noqa: E402


def make_zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, content in files.items():
            z.writestr(f"x_man-claude-branch/{name}", content)
    return b.getvalue()


def test_zip_update_replaces_code_but_keeps_user_data(tmp_path):
    (tmp_path / "mrx").mkdir()
    (tmp_path / "mrx/__init__.py").write_text('__version__ = "0.1.0"\n')
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-mine\n")
    (tmp_path / ".venv").mkdir(); (tmp_path / ".venv/keep.txt").write_text("venv")
    z = make_zip({"mrx/__init__.py": '__version__ = "9.9.9"\n', "ui/new.js": "//new", ".env": "ANTHROPIC_API_KEY=overwritten", ".venv/evil.txt": "x"})
    n = update.apply_zip(z, tmp_path)
    assert n == 2 and update.version(tmp_path) == "9.9.9" and (tmp_path / "ui/new.js").exists()
    assert (tmp_path / ".env").read_text() == "ANTHROPIC_API_KEY=sk-ant-mine\n"        # key untouched
    assert (tmp_path / ".venv/keep.txt").exists() and not (tmp_path / ".venv/evil.txt").exists()


def test_zip_slip_cannot_escape(tmp_path):
    dest = tmp_path / "proj"; dest.mkdir()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("top/../../escaped.txt", "boom"); z.writestr("top/ok.txt", "fine")
    update.apply_zip(b.getvalue(), dest)
    assert not (tmp_path / "escaped.txt").exists() and (dest / "ok.txt").read_text() == "fine"


def test_set_key_preserves_other_entries(tmp_path):
    f = tmp_path / ".env"
    f.write_text("YOUTUBE_API_KEY=yt\nANTHROPIC_API_KEY=old\n")
    set_key.write_env("ANTHROPIC_API_KEY", "sk-ant-new", f)
    assert f.read_text().splitlines() == ["YOUTUBE_API_KEY=yt", "ANTHROPIC_API_KEY=sk-ant-new"]


def test_keychain_that_cannot_read_back_is_not_reported_as_saved(rt):
    class Broken:  # accepts writes, returns nothing — the failure mode that makes a saved key "not work"
        def set_password(self, *a): ...
        def get_password(self, *a): return None
    rt.secrets._kr = Broken()
    assert rt.secrets.set("ANTHROPIC_API_KEY", "sk-ant-x") is False

    class Good:
        d = {}
        def set_password(self, s, n, v): self.d[n] = v
        def get_password(self, s, n): return self.d.get(n)
    rt.secrets._kr = Good()
    assert rt.secrets.set("ANTHROPIC_API_KEY", "sk-ant-x") is True and rt.agent.engine()[0] == "llm"
    assert rt.secrets.locate("ANTHROPIC_API_KEY") == ["keychain"]
    rt.secrets.delete("ANTHROPIC_API_KEY")
