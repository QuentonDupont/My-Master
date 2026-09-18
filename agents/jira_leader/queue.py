"""Jira Leader — the standing agent that owns the queue and the ledger.

Polls the intake board, applies the re-processing gate (invariant 4), claims
each ticket and spawns up to 5 ephemeral workers, one per ticket. Workers are
terminated on completion; nothing is long-lived except this loop.

    python -m agents.jira_leader.queue run --file tests/fixtures/inbox.jsonl
    python -m agents.jira_leader.queue run            # live, reads PESD1
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.historian.retrieval import Historian
from agents.jira_leader import analysis as analysis_mod, worker as worker_mod
from agents.jira_leader.sources import (FileTicketSource, JiraTicketSource,
                                        TicketSource, comment_count)
from core import ledger as ledger_mod, log, requester as requester_mod

LOG = log.get("jira_leader")

MAX_WORKERS = 5


def hash_of(issue: dict) -> str:
    f = issue.get("fields", {}) or {}
    return ledger_mod.content_hash(f.get("summary") or "", f.get("description") or "",
                                   comment_count(issue))


def _run_one(issue: dict, analyst_kind: str, sheet: list[dict], rules: str) -> dict:
    """Runs on a worker thread: its own ledger + corpus connections."""
    with ledger_mod.Ledger() as led, Historian() as hist:
        outcome = worker_mod.process(issue, ledger=led, historian=hist,
                                     analyst=analysis_mod.get_analyst(analyst_kind),
                                     sheet=sheet, rules=rules)
    return outcome.to_dict()


def run(source: TicketSource, *, limit: int | None = None,
        max_workers: int = MAX_WORKERS, analyst_kind: str = "auto",
        sheet_path: str | None = None) -> dict:
    issues = source.open_tickets()
    if limit:
        issues = issues[:limit]
    sheet = requester_mod.load_sheet(sheet_path)
    rules = analysis_mod.load_rules()

    claimed: list[dict] = []
    skipped: list[dict] = []
    with ledger_mod.Ledger() as led:
        for issue in issues:
            key = issue["key"]
            status = ((issue.get("fields", {}) or {}).get("status") or {}).get("name", "")
            hash_ = hash_of(issue)
            ok, reason = led.should_process(key, hash_, status)
            if not ok:
                skipped.append({"ticket": key, "reason": reason})
                continue
            if not led.claim(key, hash_):
                skipped.append({"ticket": key, "reason": "claim lost to another worker"})
                continue
            claimed.append(issue)

    LOG.info("queue.claimed", claimed=len(claimed), skipped=len(skipped),
             workers=min(max_workers, max(len(claimed), 1)))

    outcomes: list[dict] = []
    if claimed:
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="jira-worker") as pool:
            futures = {pool.submit(_run_one, issue, analyst_kind, sheet, rules):
                       issue["key"] for issue in claimed}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    outcomes.append(future.result())
                except Exception as exc:  # pragma: no cover - defensive
                    LOG.error("queue.worker_failed", ticket=key, error=str(exc))
                    outcomes.append({"ticket": key, "state": "ERROR",
                                     "proposal_id": None, "reason": str(exc)})

    summary = {"claimed": len(claimed), "skipped": skipped,
               "outcomes": sorted(outcomes, key=lambda o: o["ticket"])}
    LOG.info("queue.done", **{k: len(v) if isinstance(v, list) else v
                              for k, v in summary.items()})
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.queue")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="poll, claim and triage")
    p_run.add_argument("--file", help="offline jsonl source instead of Jira")
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

    source: TicketSource = (FileTicketSource(args.file) if args.file
                            else JiraTicketSource())
    summary = run(source, limit=args.limit, max_workers=args.workers,
                  analyst_kind=args.analyst, sheet_path=args.sheet)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
