"""Copy the M.R.X. project folder to your Desktop (…/Desktop/M.R.X).

    python scripts/place_on_desktop.py            # copy
    python scripts/place_on_desktop.py --install  # copy, then create a venv and install dependencies

Works on Windows (including OneDrive-redirected Desktops), macOS and Linux."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "*.pyc", ".mrx", "*.db")


def desktop_dir() -> Path:
    home = Path.home()
    for cand in (home / "OneDrive" / "Desktop", home / "Desktop"):
        if cand.is_dir():
            return cand
    return home / "Desktop"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="create a virtualenv and install dependencies after copying")
    ap.add_argument("--dest", help="override the destination folder")
    a = ap.parse_args()
    dest = Path(a.dest).expanduser() if a.dest else desktop_dir() / "M.R.X"
    if dest.resolve() == ROOT.resolve():
        print(f"Already at {dest}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT, dest, ignore=IGNORE, dirs_exist_ok=True)
        print(f"Copied M.R.X. to {dest}")
    if a.install:
        venv = dest / ".venv"
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
        py = venv / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
        subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(dest / "requirements.txt")])
        subprocess.call([str(py), "-m", "playwright", "install", "chromium"])
        print("Installed. Start with run.bat (Windows) or ./run.sh")
    else:
        print("Next: run install.bat (Windows) or ./install.sh, then run.bat / ./run.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
