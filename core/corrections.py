"""Append-only log of human edits to proposals — the learning loop's memory.

One line per changed field: {proposal_id, field, was, became, reason, ts}.
Nothing here ever rewrites history; the Chief of Staff only reads it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any, Iterable

from core import config, log

LOG = log.get("corrections")


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def record(proposal_id: str, field: str, was: Any, became: Any, reason: str = "") -> dict:
    entry = {"proposal_id": proposal_id, "field": field, "was": was,
             "became": became, "reason": reason, "ts": _ts()}
    config.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    with config.CORRECTIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(log.redact(entry), ensure_ascii=False, default=str) + "\n")
    LOG.info("correction.recorded", proposal=proposal_id, field=field)
    return entry


def record_many(proposal_id: str, changes: Iterable[tuple[str, Any, Any]],
                reason: str = "") -> list[dict]:
    return [record(proposal_id, f, was, became, reason) for f, was, became in changes]


def record_rejection(proposal_id: str, reason: str) -> dict:
    return record(proposal_id, "__rejected__", None, None, reason)


def load() -> list[dict]:
    if not config.CORRECTIONS.exists():
        return []
    out = []
    for line in config.CORRECTIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.corrections")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("fields", help="count corrections per field")
    args = ap.parse_args(argv)
    entries = load()
    if args.cmd == "list":
        for e in entries:
            print(f"{e['ts']}  {e['proposal_id']}  {e['field']}")
    else:
        counts: dict[str, int] = {}
        for e in entries:
            counts[e["field"]] = counts.get(e["field"], 0) + 1
        print(json.dumps(dict(sorted(counts.items(), key=lambda kv: -kv[1])), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
