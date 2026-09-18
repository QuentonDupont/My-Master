"""Things for the human to do in Jira that the system will not do itself.

The system does not delete tickets. When a clone turns out to be unnecessary —
a duplicate of work already underway, a rolled-back execution — it records a
recommendation here and surfaces it for review. Closing it is the human's
action, in Jira, with their own judgement.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, log

LOG = log.get("recommendations")

def _store() -> Path:
    """Resolved on every call — a module-level constant would bind the real
    review directory at import and ignore a redirected one."""
    return config.REVIEW_DIR / "recommendations.jsonl"

CLOSE = "Close as Won't Do"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def add(ticket: str, action: str, reason: str, *, related: list[str] | None = None,
        source_ticket: str | None = None) -> dict:
    entry = {
        "id": f"{ticket}:{action}".replace(" ", "_"),
        "ticket": ticket,
        "ticket_url": config.ticket_url(ticket),
        "action": action,
        "reason": reason,
        "related": related or [],
        "source_ticket": source_ticket,
        "state": "open",
        "ts": _now(),
    }
    existing = {e["id"] for e in load()}
    if entry["id"] in existing:
        LOG.info("recommendation.duplicate", ticket=ticket, action=action)
        return entry
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    LOG.info("recommendation.added", ticket=ticket, action=action)
    return entry


def recommend_close(ticket: str, reason: str, *, related: list[str] | None = None,
                    source_ticket: str | None = None) -> dict:
    return add(ticket, CLOSE, reason, related=related, source_ticket=source_ticket)


def load() -> list[dict]:
    store = _store()
    if not store.exists():
        return []
    return [json.loads(line) for line in
            store.read_text(encoding="utf-8").splitlines() if line.strip()]


def open_items() -> list[dict]:
    return [e for e in load() if e.get("state") == "open"]


def resolve(entry_id: str, state: str = "done") -> bool:
    """Mark one recommendation done or dismissed. Rewrites the file in place."""
    entries = load()
    found = False
    for entry in entries:
        if entry["id"] == entry_id:
            entry["state"] = state
            entry["resolved_ts"] = _now()
            found = True
    if found:
        _store().write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
            encoding="utf-8")
        LOG.info("recommendation.resolved", id=entry_id, state=state)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.recommendations")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_add = sub.add_parser("close", help="recommend closing a ticket as Won't Do")
    p_add.add_argument("ticket")
    p_add.add_argument("--reason", required=True)
    p_add.add_argument("--related", nargs="*", default=[])
    p_res = sub.add_parser("resolve")
    p_res.add_argument("id")
    p_res.add_argument("--state", default="done", choices=["done", "dismissed"])
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for entry in load():
            mark = "·" if entry["state"] == "open" else "✓"
            print(f"{mark} {entry['ticket']:<12} {entry['action']:<20} {entry['reason'][:60]}")
    elif args.cmd == "close":
        print(json.dumps(recommend_close(args.ticket, args.reason,
                                         related=args.related), indent=2))
    else:
        print("resolved" if resolve(args.id, args.state) else "not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
