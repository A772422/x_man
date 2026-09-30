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
    args = ap.parse_args()

    import uvicorn
    from .api.app import create_app

    token = os.environ.get("MRX_TOKEN") or secrets.token_urlsafe(24)
    app = create_app(token=token)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  M.R.X. running at {url}\n  (bound to 127.0.0.1 only; requests need this launch's token)\n")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
