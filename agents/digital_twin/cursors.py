"""Where each source was last read, so an update never repeats old items.

Starter file, section D: keep a last-check time per source with its time zone
and any gap; never advance a failed source's cursor; dedupe on exact ids.

    python -m agents.digital_twin.cursors show
    python -m agents.digital_twin.cursors reset slack
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, log

LOG = log.get("digital_twin")

#: how many item ids to remember per source for dedupe
SEEN_LIMIT = 2000


def path() -> Path:
    return config.TWIN_DIR / "cursors.json"


def _load() -> dict:
    p = path()
    if not p.exists():
        return {"sources": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        LOG.warn("cursors.unreadable", path=str(p))
        return {"sources": {}}


def _save(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def get(source: str) -> dict:
    """{"through": iso, "tz": name, "gap": text|None, "seen": [ids]}"""
    return _load()["sources"].get(source) or {"through": "", "tz": "", "gap": None,
                                              "seen": []}


def since(source: str, default_lookback: dt.timedelta = dt.timedelta(days=1)) -> dt.datetime:
    """When to read from. The first time a source is seen, one day back."""
    cur = get(source)
    if cur["through"]:
        try:
            return dt.datetime.fromisoformat(cur["through"])
        except ValueError:
            pass
    return dt.datetime.now().astimezone() - default_lookback


def unseen(source: str, item_ids: list[str]) -> list[str]:
    seen = set(get(source)["seen"])
    return [i for i in item_ids if i not in seen]


def advance(source: str, through: dt.datetime, item_ids: list[str],
            tz: str = "") -> None:
    """Record a successful read. Call this only after the items were used."""
    data = _load()
    cur = data["sources"].get(source) or {"seen": []}
    seen = list(cur.get("seen") or []) + list(item_ids)
    data["sources"][source] = {
        "through": through.isoformat(timespec="seconds"),
        "tz": tz or (through.tzname() or ""),
        "gap": None,
        "seen": seen[-SEEN_LIMIT:],
    }
    _save(data)


def mark_failed(source: str, reason: str) -> None:
    """A failed read keeps its cursor and records the gap, so the next
    update can say "not checked since <when>" instead of pretending."""
    data = _load()
    cur = data["sources"].get(source) or {"through": "", "tz": "", "seen": []}
    cur["gap"] = f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')}: {reason[:200]}"
    data["sources"][source] = cur
    _save(data)
    LOG.warn("cursors.failed", source=source, reason=reason[:200])


def reset(source: str | None = None) -> None:
    data = _load()
    if source:
        data["sources"].pop(source, None)
    else:
        data["sources"] = {}
    _save(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-source read cursors.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    r = sub.add_parser("reset")
    r.add_argument("source", nargs="?")
    args = ap.parse_args(argv)
    if args.cmd == "show":
        for name, cur in sorted(_load()["sources"].items()):
            print(f"{name:10} through {cur.get('through') or 'never':32} "
                  f"{cur.get('tz') or ''}  seen={len(cur.get('seen') or [])}"
                  + (f"  GAP {cur['gap']}" if cur.get("gap") else ""))
        return 0
    if args.cmd == "reset":
        reset(args.source)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
