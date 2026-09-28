"""Keep a team board in priority order.

The board owner asked (28 Sep 2026) for the team's board to be set up by
priority, at least once a day, so the next ticket to pick up is always the top
of its column.

What it does
------------
Takes every active ticket on each board in `config/boards.yml: rank_boards`,
in the board's current rank order, and sorts it by priority — Critical, High,
Medium, Low, then anything without one. The sort is **stable**: within one
priority the team's existing order is kept, so a hand-ordered column is not
reshuffled. A board already in order is left alone.

Why this is not a proposal
--------------------------
Invariant 1 routes every Jira write through `execute_proposal`. Re-ranking is
not a per-ticket decision there is anything to propose about. It is the same
kind of bookkeeping as status mirroring, and it is exactly this narrow:

* the Rank field only — never a status, field, comment, link or assignment
* only boards listed in `rank_boards`, only issues on an allowed project
* the full prior order is recorded before anything moves, so `undo` puts the
  board back exactly as it was

Unattended runs (`--execute` from launchd) need that exception written into
CLAUDE.md by the board owner, like the status-mirror one. Until then, run it
by hand.

    python -m core.board_rank plan                 # what would move, per board
    python -m core.board_rank apply [--execute]    # dry run unless --execute
    python -m core.board_rank undo <run_id> [--execute]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, log

LOG = log.get("board_rank")

#: highest first
PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"]
#: statuses that are finished work: not re-ranked
DONE_STATUSES = ("Done", "Live", "Closed - Won't Do")
BATCH = 50


def _history_path() -> Path:
    return config.REVIEW_DIR / "rank_history.jsonl"


def priority_rank(name: str | None) -> int:
    try:
        return PRIORITY_ORDER.index(name or "")
    except ValueError:
        return len(PRIORITY_ORDER)


def target_order(rows: list[dict]) -> list[str]:
    """Keys sorted by priority; ties keep their current relative order."""
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda pair: (priority_rank(pair[1].get("priority")), pair[0]))
    return [row["key"] for _, row in indexed]


def moves(order: list[str]) -> list[dict]:
    """Rank calls that lay `order` out top to bottom.

    The first key goes before whatever is currently first; the rest follow it
    in batches of 50, each batch after the last key placed.
    """
    return [{"keys": order[i:i + BATCH], "after": order[i - 1]}
            for i in range(1, len(order), BATCH)]


def collect(board_id: int, client=None) -> list[dict]:
    """Active issues on the board, in current rank order."""
    from core.jira_client import JiraReadClient
    client = client or JiraReadClient()
    client.board_configuration(board_id)  # refuses a board outside scope
    done = ", ".join(f'"{s}"' for s in DONE_STATUSES)
    issues = client.board_issues(
        board_id, jql=f"status not in ({done}) AND issuetype != Epic ORDER BY Rank ASC",
        fields=["summary", "priority", "status", "issuetype"])
    # Sub-tasks are left out: Jira keeps them with their parent and refuses to
    # rank one among other issues, so the parent's position decides theirs.
    return [{"key": i["key"],
             "priority": ((i.get("fields") or {}).get("priority") or {}).get("name"),
             "status": ((i.get("fields") or {}).get("status") or {}).get("name")}
            for i in issues
            if not ((i.get("fields") or {}).get("issuetype") or {}).get("subtask")]


def plan(board_id: int, rows: list[dict] | None = None) -> dict:
    rows = collect(board_id) if rows is None else rows
    current = [r["key"] for r in rows]
    target = target_order(rows)
    out_of_place = sum(1 for a, b in zip(current, target) if a != b)
    return {"board": board_id, "issues": len(rows), "out_of_place": out_of_place,
            "current": current, "target": target,
            "by_priority": {p: sum(1 for r in rows if (r.get("priority") or "none") == p)
                            for p in [*PRIORITY_ORDER, "none"]}}


def _lay_out(order: list[str], current_top: str, writer, rank_field: int | None) -> list:
    results = []
    if order[0] != current_top:
        results.append(writer.rank_issues([order[0]], before=current_top,
                                          rank_field=rank_field))
    for move in moves(order):
        results.append(writer.rank_issues(move["keys"], after=move["after"],
                                          rank_field=rank_field))
    return results


def _record(entry: dict) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def apply(board_id: int, *, execute: bool = False, rows: list[dict] | None = None,
          writer=None, rank_field: int | None = None) -> dict:
    from core.jira_client import JiraWriteClient
    p = plan(board_id, rows)
    if p["current"] == p["target"]:
        LOG.info("board_rank.in_order", board=board_id, issues=p["issues"])
        return {**p, "applied": False, "reason": "already in priority order"}
    writer = writer or JiraWriteClient(execute=execute)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{board_id}"
    if execute:  # record the prior order before anything moves
        _record({"run_id": run_id, "board": board_id, "prior": p["current"],
                 "applied": p["target"], "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
    results = _lay_out(p["target"], p["current"][0], writer, rank_field)
    # The rank endpoint answers 207 with per-issue failures rather than raising.
    errors = [e for r in results if isinstance(r, dict)
              for e in (r.get("entries") or []) if e.get("status", 200) >= 400]
    if errors:
        LOG.warn("board_rank.partial", board=board_id, errors=errors[:10])
    LOG.info("board_rank.applied" if execute else "board_rank.dry_run",
             board=board_id, moved=p["out_of_place"], run_id=run_id)
    return {**p, "applied": execute, "run_id": run_id if execute else None,
            "rank_errors": errors}


def undo(run_id: str, *, execute: bool = False, writer=None,
         rank_field: int | None = None) -> dict:
    from core.jira_client import JiraWriteClient
    entries = [json.loads(line) for line in _history_path().read_text().splitlines()
               if line.strip()] if _history_path().exists() else []
    entry = next((e for e in entries if e["run_id"] == run_id), None)
    if not entry:
        raise SystemExit(f"no rank run {run_id!r} in {_history_path()}")
    writer = writer or JiraWriteClient(execute=execute)
    _lay_out(entry["prior"], entry["applied"][0], writer, rank_field)
    return {"run_id": run_id, "restored": len(entry["prior"]), "executed": execute}


def _boards() -> list[dict]:
    return list(config.boards().get("rank_boards") or [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.board_rank")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    a = sub.add_parser("apply")
    a.add_argument("--execute", action="store_true")
    u = sub.add_parser("undo")
    u.add_argument("run_id")
    u.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "undo":
        board = next((b for b in _boards() if args.run_id.endswith(f"-{b['id']}")), {})
        print(json.dumps(undo(args.run_id, execute=args.execute,
                              rank_field=board.get("rank_field")), indent=2))
        return 0
    for board in _boards():
        if args.cmd == "plan":
            result = plan(board["id"])
        else:
            result = apply(board["id"], execute=args.execute,
                           rank_field=board.get("rank_field"))
        result.pop("current", None)
        result["target_top10"] = result.pop("target")[:10]
        print(json.dumps({"name": board.get("name"), **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
