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


PROVIDERS = {
    "1": ("GEMINI_API_KEY", "gemini", ("AIza", "AQ."), "Google Gemini (free key: https://aistudio.google.com/apikey)"),
    "2": ("ANTHROPIC_API_KEY", "anthropic", "sk-ant-", "Anthropic Claude (paid API credits: https://console.anthropic.com/)"),
}


def main() -> int:
    print("\n  M.R.X. — AI key setup\n")
    for k, (_, _, _, label) in PROVIDERS.items():
        print(f"    {k}) {label}")
    choice = input("\n  Which one? Enter 1 or 2 [1]: ").strip() or "1"
    if choice not in PROVIDERS:
        print("  Please enter 1 or 2.")
        return 1
    name, provider, prefix, label = PROVIDERS[choice]
    print(f"\n  It will be saved to:  {ENV}\n")
    key = input(f"  Paste your {label.split(' (')[0]} key here and press Enter: ").strip().strip('"').strip("'")
    if not key:
        print("\n  Nothing entered — nothing changed.")
        return 1
    starts = " or ".join(prefix) if isinstance(prefix, tuple) else prefix
    if not key.startswith(prefix) or " " in key:
        print(f"\n  That does not look like a {label.split(' (')[0]} key (expected to start with {starts} and contain no spaces).")
        if input("  Save it anyway? [y/N] ").strip().lower() != "y":
            return 1
    if key.startswith("AQ."):
        print("\n  NOTE: keys starting with 'AQ.' are Google's newer key type; many Gemini endpoints reject them (a known Google issue).")
        print("  If the test below fails, create an 'AIza...' key: open https://aistudio.google.com/apikey in an INCOGNITO window,")
        print("  delete the AQ. keys, click Create API key. Alternative: Google Cloud Console > APIs & Services > Credentials > Create API key,")
        print("  and enable the 'Generative Language API' for that project.")
    write_env(name, key)
    sys.path.insert(0, str(ROOT))
    os.environ[name] = key
    try:
        from mrx.core.config import Settings
        Settings().update({"ai": {"provider": provider}})   # use the provider you just configured first
    except Exception as e:  # noqa: BLE001 - preference is a convenience only
        print(f"  (could not save the provider preference: {e})")
    print("\n  Saved. Testing the connection …\n")
    from mrx.doctor import run
    code = run()
    print("\n  Now start M.R.X. with run.bat — it will pick the key up automatically." if code == 0
          else "\n  The key was saved but the test failed (see above). Fix the problem shown, or run setup_key.bat again.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
