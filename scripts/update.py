"""Update this M.R.X. folder to the latest version from GitHub (keeps your .env, .venv and data).

    python scripts/update.py            (or double-click update.bat)

* Folder made with `git clone`  -> `git pull`.
* Folder from a ZIP download    -> downloads the latest ZIP and copies the new files over the old ones.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "A772422/x_man"
BRANCH = "claude/nice-einstein-nhlhj1"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
KEEP = {".venv", ".env", ".git", ".mrx", "__pycache__", ".pytest_cache", "node_modules"}


def apply_zip(zip_bytes: bytes, dest: Path) -> int:
    """Copy every file from the ZIP over `dest`, skipping user data. Returns the number of files written."""
    n = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z, tempfile.TemporaryDirectory() as tmp:
        z.extractall(tmp)  # zipfile strips absolute paths and '..' components on extraction
        tops = [p for p in Path(tmp).iterdir() if p.is_dir()]
        src = tops[0] if len(tops) == 1 else Path(tmp)
        for f in src.rglob("*"):
            rel = f.relative_to(src)
            if f.is_dir() or any(part in KEEP for part in rel.parts):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            n += 1
    return n


def version(root: Path) -> str:
    try:
        return next(l.split('"')[1] for l in (root / "mrx" / "__init__.py").read_text("utf-8").splitlines() if l.startswith("__version__"))
    except Exception:
        return "unknown"


def main() -> int:
    before = version(ROOT)
    print(f"\n  M.R.X. update — current version: {before}\n  Folder: {ROOT}\n")
    if (ROOT / ".git").exists() and shutil.which("git"):
        print("  This folder is a git clone: running  git pull …\n")
        r = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only", "origin", BRANCH])
        if r.returncode != 0:
            print("\n  git pull failed. If you edited files yourself, run  git stash  first, then update.bat again.")
            return 1
    else:
        print(f"  Downloading the latest version from GitHub ({REPO}, branch {BRANCH}) …")
        try:
            with urllib.request.urlopen(urllib.request.Request(ZIP_URL, headers={"User-Agent": "mrx-updater"}), timeout=60) as resp:
                data = resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"\n  Download failed: {e}\n  If the repository is private, install Git for Windows (https://git-scm.com/download/win),"
                  f"\n  then run:  git clone -b {BRANCH} https://github.com/{REPO}.git   and use that folder from now on.")
            return 1
        n = apply_zip(data, ROOT)
        print(f"  Updated {n} files.")
    print("\n  Installing any new dependencies …")
    subprocess.call([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    print(f"\n  Done. Version: {before} -> {version(ROOT)}\n  Close M.R.X. if it is running and start it again with run.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
