"""`python -m mrx` — start M.R.X. and open the interface."""
from __future__ import annotations

import argparse
import os
import secrets
import threading
from pathlib import Path
import time
import webbrowser


def load_env_files(paths) -> list[str]:
    """Minimal .env loader (KEY=VALUE, # comments, optional quotes). Never overrides variables already set."""
    loaded = []
    for p in paths:
        try:
            lines = Path(p).read_text("utf-8-sig").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().removeprefix("export ").strip(), v.strip().strip("\"'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
                loaded.append(k)
    return loaded


def check_port(port: int) -> str | None:
    """None if free; 'mrx' if another M.R.X. answers there; 'other' if something else uses the port."""
    import socket
    with socket.socket() as sk:
        sk.settimeout(0.5)
        if sk.connect_ex(("127.0.0.1", port)) != 0:
            return None
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            return "mrx" if b"M.R.X." in r.read(20000) else "other"
    except Exception:
        return "other"


def main() -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from .core.config import data_dir
    loaded = load_env_files([Path(__file__).resolve().parents[1] / ".env", data_dir() / ".env"])
    if loaded:
        print(f"  Loaded from .env: {', '.join(loaded)}")
    ap = argparse.ArgumentParser(description="M.R.X. — real-time autonomous desktop AI agent")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MRX_PORT", 8765)))
    ap.add_argument("--no-browser", action="store_true", help="do not open the interface automatically")
    ap.add_argument("--doctor", action="store_true", help="print diagnostics and test the AI connection, then exit")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()
    if args.version:
        from . import __version__
        print(__version__)
        return
    if args.doctor:
        from .doctor import run
        raise SystemExit(run())

    busy = check_port(args.port)
    if busy:
        who = ("An OLD copy of M.R.X. is still running" if busy == "mrx" else f"Another program is using port {args.port}")
        print(f"\n  ✗ {who}, so this new copy cannot start.\n"
              f"    Close the other M.R.X. window (click it and press Ctrl+C, or just close it) and any open M.R.X. browser tabs,\n"
              f"    then run run.bat again.  (Or start on another port:  run.bat --port {args.port + 1})\n")
        raise SystemExit(1)

    import uvicorn
    from .api.app import create_app

    token = os.environ.get("MRX_TOKEN") or secrets.token_urlsafe(24)
    app = create_app(token=token)
    url = f"http://127.0.0.1:{args.port}/"
    from . import __version__
    print(f"\n  M.R.X. {__version__} running at {url}\n  (bound to 127.0.0.1 only; requests need this launch's token)\n")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
