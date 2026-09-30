"""Secret redaction, secret storage (OS keychain -> env), and secret detection."""
from __future__ import annotations

import os
import re
from typing import Any

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

    def __init__(self):
        try:
            import keyring  # type: ignore
            keyring.get_keyring()
            self._kr = keyring
        except Exception:
            self._kr = None

    @property
    def backend(self) -> str:
        return "os-keychain" if self._kr else "environment-only"

    def get(self, name: str) -> str | None:
        env = os.environ.get(name)
        if env:
            return env
        if self._kr:
            try:
                return self._kr.get_password(self.SERVICE, name)
            except Exception:
                return None
        return None

    def set(self, name: str, value: str) -> bool:
        if not self._kr:
            return False
        try:
            self._kr.set_password(self.SERVICE, name, value)
            return True
        except Exception:
            return False

    def delete(self, name: str) -> None:
        if self._kr:
            try:
                self._kr.delete_password(self.SERVICE, name)
            except Exception:
                pass

    def has(self, name: str) -> bool:
        return bool(self.get(name))
