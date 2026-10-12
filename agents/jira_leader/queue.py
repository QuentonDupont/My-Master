"""Intake sweep — CLI wrapper over the intake flow (flows/intake.py).

Poll the intake board, apply the re-processing gate (invariant 4), claim, and
triage each claimed ticket (up to 5 at once). Scheduled runs go through
`python -m flows tick`; this is the same flow, run by hand.

    python -m agents.jira_leader.queue run --file tests/fixtures/inbox.jsonl
    python -m agents.jira_leader.queue run            # live, reads PESD1
"""
from __future__ import annotations

import argparse
import json

from agents.jira_leader.sources import (FileTicketSource, JiraTicketSource,
                                        KeyTicketSource, TicketSource)
from core import ledger as ledger_mod, log

LOG = log.get("jira_leader")

MAX_WORKERS = 5


def hash_of(issue: dict) -> str:
    from flows.intake import hash_of as _hash_of
    return _hash_of(issue)


def run(source: TicketSource, *, limit: int | None = None,
        max_workers: int = MAX_WORKERS, analyst_kind: str = "auto",
        sheet_path: str | None = None) -> dict:
    """One intake sweep. The work is the intake flow (flows/intake.py)."""
    from flows.intake import INTAKE

    result = INTAKE.run(source=source, limit=limit, max_workers=max_workers,
                        analyst_kind=analyst_kind, sheet_path=sheet_path)
    result.raise_for_error()
    return result.data["report"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.queue")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="poll, claim and triage")
    p_run.add_argument("--file", help="offline jsonl source instead of Jira")
    p_run.add_argument("--keys", help="comma-separated ticket keys to triage "
                                      "regardless of their current status")
    p_run.add_argument("--sheet", help="requester intake sheet CSV")
    p_run.add_argument("--limit", type=int)
    p_run.add_argument("--workers", type=int, default=MAX_WORKERS)
    p_run.add_argument("--analyst", default="auto",
                       choices=["auto", "heuristic", "claude"])
    sub.add_parser("status", help="ledger counts per state")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        with ledger_mod.Ledger() as led:
            print(json.dumps(led.stats(), indent=2))
        return 0

    if args.file:
        source: TicketSource = FileTicketSource(args.file)
    elif args.keys:
        source = KeyTicketSource(args.keys.split(","))
    else:
        source = JiraTicketSource()
    summary = run(source, limit=args.limit, max_workers=args.workers,
                  analyst_kind=args.analyst, sheet_path=args.sheet)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
