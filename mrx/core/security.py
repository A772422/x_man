"""Secret redaction, secret storage (OS keychain -> env), and secret detection."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger("mrx.secrets")
SENSITIVE_KEYS = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|credential|bearer)", re.I)
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),  # JWT
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]
REDACTED = "[REDACTED]"


def redact_text(s: str) -> str:
    for p in SECRET_PATTERNS:
        s = p.sub(REDACTED, s)
    return s


def redact(obj: Any, _depth: int = 0) -> Any:
    """Deep-redact by key name and by value pattern. Also truncates huge strings."""
    if _depth > 8:
        return "[…]"
    if isinstance(obj, dict):
        return {k: (REDACTED if SENSITIVE_KEYS.search(str(k)) and v else redact(v, _depth + 1))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj[:200]]
    if isinstance(obj, str):
        s = redact_text(obj)
        return s if len(s) <= 2000 else s[:2000] + f"…[+{len(s) - 2000} chars]"
    return obj


def contains_secret(text: str) -> bool:
    if any(p.search(text) for p in SECRET_PATTERNS):
        return True
    return bool(re.search(r"(?i)\b(my\s+)?(password|passwd|pin|api[ _-]?key|secret|token)\s*(is|=|:)\s*\S+", text))


class SecretStore:
    """Secrets live in the OS keychain (keyring) when available, otherwise env vars.
    They are never written to settings.json, the database or logs."""

    SERVICE = "mrx"

    def __init__(self, home=None):
        self.env_file = (__import__("pathlib").Path(home) / ".env") if home else None
        self._mem: dict[str, str] = {}      # secrets saved during this run: usable immediately, whatever the backend does
        try:
            import keyring  # type: ignore
            keyring.get_keyring()
            self._kr = keyring
        except Exception:
            self._kr = None

    @property
    def backend(self) -> str:
        return "os-keychain" if self._kr else "environment / private .env file (no OS keychain found)"

    def _kr_get(self, name: str) -> str | None:
        if not self._kr:
            return None
        try:
            return self._kr.get_password(self.SERVICE, name)
        except Exception as e:  # a broken keychain must not crash M.R.X., but it must not be silent either
            log.warning("could not read %s from the OS keychain: %s: %s", name, type(e).__name__, e)
            return None

    def get(self, name: str) -> str | None:
        return os.environ.get(name) or self._mem.get(name) or self._kr_get(name)

    def locate(self, name: str) -> list[str]:
        """Where a secret currently comes from (never its value)."""
        found = []
        if os.environ.get(name):
            found.append("file (.env)" if self.in_file(name) else "environment")
        if self._kr_get(name):
            found.append("keychain")
        if self._mem.get(name) and not found:
            found.append("this session only")
        return found

    def set(self, name: str, value: str) -> bool:
        """Store in the OS keychain. Returns True only if the value can be read back — a keychain that accepts a
        write but cannot return it would otherwise look 'saved' while the key never takes effect."""
        if not self._kr:
            return False
        try:
            self._kr.set_password(self.SERVICE, name, value)
            back = self._kr.get_password(self.SERVICE, name)
        except Exception as e:
            log.warning("keychain write failed for %s: %s: %s", name, type(e).__name__, e)
            return False
        if back != value:
            log.warning("keychain accepted %s but could not read it back", name)
            return False
        self._mem[name] = value
        return True

    # -- fallback when no OS keychain exists: a private ~/.mrx/.env, applied to the running process at once -----
    def _file_lines(self) -> list[str]:
        try:
            return self.env_file.read_text("utf-8-sig").splitlines() if self.env_file else []
        except OSError:
            return []

    def in_file(self, name: str) -> bool:
        return any(l.strip().removeprefix("export ").split("=", 1)[0].strip() == name for l in self._file_lines() if "=" in l)

    def set_file(self, name: str, value: str) -> bool:
        if not self.env_file:
            return False
        keep = [l for l in self._file_lines() if l.strip().removeprefix("export ").split("=", 1)[0].strip() != name]
        self.env_file.write_text("\n".join(keep + [f"{name}={value}"]) + "\n", "utf-8")
        try:
            os.chmod(self.env_file, 0o600)
        except OSError:
            pass
        os.environ[name] = value
        self._mem[name] = value
        return True

    def delete(self, name: str) -> None:
        self._mem.pop(name, None)
        if self.in_file(name):
            keep = [l for l in self._file_lines() if l.strip().removeprefix("export ").split("=", 1)[0].strip() != name]
            self.env_file.write_text("\n".join(keep) + ("\n" if keep else ""), "utf-8")
            os.environ.pop(name, None)
        if self._kr:
            try:
                self._kr.delete_password(self.SERVICE, name)
            except Exception:
                pass

    def has(self, name: str) -> bool:
        return bool(self.get(name))
