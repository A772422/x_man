"""Save your Anthropic API key to the .env file in the M.R.X. folder, then test it.

    python scripts/set_key.py            (or double-click setup_key.bat)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def write_env(name: str, value: str, path: Path = ENV) -> None:
    lines = []
    if path.exists():
        lines = [l for l in path.read_text("utf-8-sig").splitlines()
                 if l.strip().removeprefix("export ").split("=", 1)[0].strip() != name]
    path.write_text("\n".join(lines + [f"{name}={value}"]) + "\n", "utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    print("\n  M.R.X. — Anthropic API key setup")
    print("  Get a key at https://console.anthropic.com/  (it starts with  sk-ant-)")
    print(f"  It will be saved to:  {ENV}\n")
    key = input("  Paste your API key here and press Enter: ").strip().strip('"').strip("'")
    if not key:
        print("\n  Nothing entered — nothing changed.")
        return 1
    if not key.startswith("sk-ant-") or " " in key:
        print("\n  That does not look like an Anthropic key (it should start with sk-ant- and contain no spaces).")
        if input("  Save it anyway? [y/N] ").strip().lower() != "y":
            return 1
    write_env("ANTHROPIC_API_KEY", key)
    print("\n  Saved. Testing the connection …\n")
    sys.path.insert(0, str(ROOT))
    os.environ["ANTHROPIC_API_KEY"] = key
    from mrx.doctor import run
    code = run()
    print("\n  Now start M.R.X. with run.bat — it will pick the key up automatically." if code == 0
          else "\n  The key was saved but the test failed (see above). Fix the problem shown, or run setup_key.bat again.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
