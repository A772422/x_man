#!/usr/bin/env sh
# M.R.X. installer (macOS / Linux)
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.10+ is required"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt || echo "Some optional packages could not be installed; M.R.X. still works."
python -m playwright install chromium || echo "Browser download failed; browser automation will be unavailable."
echo "Done. Start with ./run.sh"
