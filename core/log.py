"""Structured JSONL logging to logs/. Secrets are redacted on the way out."""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from typing import Any

from core import config

_lock = threading.Lock()
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def redact(value: Any) -> Any:
    """Replace any known secret substring with ***. Applied to every log field."""
    if isinstance(value, str):
        out = value
        for secret in config.known_secrets():
            if secret and len(secret) >= 8 and secret in out:
                out = out.replace(secret, "***")
        return out
    if isinstance(value, dict):
        return {k: ("***" if _sensitive_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _sensitive_key(key: str) -> bool:
    k = str(key).upper()
    return any(h in k for h in ("TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION", "API_KEY"))


class Logger:
    def __init__(self, name: str) -> None:
        self.name = name
        self.min_level = _LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").upper(), 20)
        self.path = config.LOG_DIR / f"{name}.jsonl"

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        if _LEVELS[level] < self.min_level:
            return
        record = {"ts": _now(), "level": level, "logger": self.name, "event": event}
        record.update(redact(fields))
        line = json.dumps(record, default=str, ensure_ascii=False)
        with _lock:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if os.environ.get("LOG_STDERR", "1") == "1":
                import sys

                print(line, file=sys.stderr)

    def debug(self, event: str, **f: Any) -> None:
        self._emit("DEBUG", event, **f)

    def info(self, event: str, **f: Any) -> None:
        self._emit("INFO", event, **f)

    def warn(self, event: str, **f: Any) -> None:
        self._emit("WARN", event, **f)

    def error(self, event: str, **f: Any) -> None:
        self._emit("ERROR", event, **f)


def get(name: str) -> Logger:
    return Logger(name)
