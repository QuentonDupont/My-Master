"""Paths, .env loading and config/*.yml access.

Nothing in this repo reads os.environ directly for a secret; everything goes
through `env()` so the redactor in core.log knows every secret value.
"""
from __future__ import annotations

import os
import pathlib
import threading
from functools import lru_cache

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CORPUS_DIR = ROOT / "corpus"
KNOWLEDGE_DIR = ROOT / "knowledge"
REVIEW_DIR = ROOT / "review"
LOG_DIR = ROOT / "logs"

LEDGER_DB = ROOT / "corpus" / "ledger.db"
CORPUS_DB = ROOT / "corpus" / "corpus.db"
CORRECTIONS = KNOWLEDGE_DIR / "corrections.jsonl"
RULES_MD = KNOWLEDGE_DIR / "rules.md"

_SECRET_HINTS = ("TOKEN", "KEY", "SECRET", "PASSWORD")
_secrets: set[str] = set()
_lock = threading.Lock()


def load_env(path: pathlib.Path | None = None) -> None:
    """Load .env into os.environ. Existing environment always wins."""
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    load_env()
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"{name} is not set. Copy config/.env.example to .env and fill it in."
        )
    value = value or ""
    if value and any(h in name.upper() for h in _SECRET_HINTS):
        with _lock:
            _secrets.add(value)
    return value


def known_secrets() -> set[str]:
    with _lock:
        return set(_secrets)


def load_yaml(path: pathlib.Path):
    text = path.read_text()
    try:  # PyYAML if the operator happens to have it; otherwise the bundled subset.
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        from core import miniyaml

        return miniyaml.loads(text)


@lru_cache(maxsize=None)
def boards() -> dict:
    return load_yaml(CONFIG_DIR / "boards.yml")


@lru_cache(maxsize=None)
def never_touch() -> dict:
    return load_yaml(CONFIG_DIR / "never_touch.yml")


@lru_cache(maxsize=None)
def repos() -> dict:
    return load_yaml(CONFIG_DIR / "repos.yml")


def intake_project() -> str:
    return boards()["intake"]["project"]


def dev_project() -> str:
    return boards()["development"]["project"]


def allowed_projects() -> list[str]:
    return list(boards()["allowed_projects"])


def base_url() -> str:
    return env("JIRA_BASE_URL", boards()["intake"]["base_url"]).rstrip("/")


def ticket_url(key: str) -> str:
    return f"{base_url()}/browse/{key}"


if __name__ == "__main__":  # python -m core.config
    import json

    print(json.dumps({
        "root": str(ROOT),
        "intake": intake_project(),
        "dev": dev_project(),
        "allowed": allowed_projects(),
        "base_url": base_url(),
        "ledger_db": str(LEDGER_DB),
        "corpus_db": str(CORPUS_DB),
        "env_file_present": (ROOT / ".env").exists(),
        "never_touch_categories": sorted(never_touch()["categories"]),
    }, indent=2))
