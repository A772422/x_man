#!/usr/bin/env sh
cd "$(dirname "$0")"
[ -d .venv ] || { echo "Run ./install.sh first"; exit 1; }
. .venv/bin/activate
exec python -m mrx "$@"
