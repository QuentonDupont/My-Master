"""Track the boards and rank what is open by priority.

Read-only. Every board listed in `config/boards.yml: tracked_boards` must belong
to an allowed project; the client checks each issue it returns, so a board whose
filter reaches wider raises rather than widening scope silently.

    python -m agents.chief_of_staff.boards rank --top 20
    python -m agents.chief_of_staff.boards snapshot      # record today's state
    python -m agents.chief_of_staff.boards changes       # against the last snapshot
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, log
from core.jira_client import JiraReadClient

LOG = log.get("boards")

#: highest first — the order the human should work in
PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"]
UNRANKED = "Unprioritised"

def _snapshots_path() -> Path:
    """Resolved per call, so a redirected review directory is honoured."""
    return config.REVIEW_DIR / "board_snapshots.jsonl"

FIELDS = ["summary", "status", "priority", "assignee", "created", "updated",
          "issuetype", "labels", "resolution"]


def _terminal_statuses(project: str) -> list[str]:
    boards = config.boards()
    section = "intake" if project == config.intake_project() else "development"
    return list(boards[section].get("resolved_statuses") or [])


def _open_jql(project: str) -> str:
    terminal = _terminal_statuses(project)
    if not terminal:
        return "resolution is EMPTY"
    names = ", ".join(f'"{s}"' for s in terminal)
    return f"status not in ({names}) AND resolution is EMPTY"


def priority_rank(name: str | None) -> int:
    try:
        return PRIORITY_ORDER.index(name or "")
    except ValueError:
        return len(PRIORITY_ORDER)


def _age_days(created: str) -> int:
    try:
        made = dt.datetime.strptime((created or "")[:10], "%Y-%m-%d")
    except ValueError:
        return 0
    return (dt.datetime.now() - made).days


def collect(client: JiraReadClient | None = None) -> list[dict]:
    """Every open ticket across the tracked boards, ranked highest priority first."""
    client = client or JiraReadClient()
    rows: list[dict] = []
    for board in (config.boards().get("tracked_boards") or []):
        project = board["project"]
        issues = client.board_issues(board["id"], jql=_open_jql(project), fields=FIELDS)
        for issue in issues:
            f = issue["fields"]
            rows.append({
                "key": issue["key"],
                "board": board.get("name") or str(board["id"]),
                "board_id": board["id"],
                "project": project,
                "priority": ((f.get("priority") or {}) or {}).get("name") or UNRANKED,
                "status": ((f.get("status") or {}) or {}).get("name") or "?",
                "assignee": ((f.get("assignee") or {}) or {}).get("displayName") or "—",
                "type": ((f.get("issuetype") or {}) or {}).get("name") or "?",
                "summary": f.get("summary") or "",
                "created": (f.get("created") or "")[:10],
                "updated": (f.get("updated") or "")[:10],
                "age_days": _age_days(f.get("created") or ""),
                "url": config.ticket_url(issue["key"]),
            })
    # Highest priority first, then the one that has been waiting longest.
    rows.sort(key=lambda r: (priority_rank(r["priority"]), -r["age_days"], r["key"]))
    LOG.info("boards.collected", tickets=len(rows),
             boards=[b["id"] for b in (config.boards().get("tracked_boards") or [])])
    return rows


def summarise(rows: list[dict]) -> dict:
    by_priority: dict[str, int] = {}
    by_board: dict[str, dict[str, int]] = {}
    unassigned = 0
    for row in rows:
        # Tolerant of partial rows: a summary must never be the thing that breaks
        # the morning brief.
        priority = row.get("priority") or UNRANKED
        by_priority[priority] = by_priority.get(priority, 0) + 1
        board = by_board.setdefault(row.get("board") or "unknown board", {})
        board[priority] = board.get(priority, 0) + 1
        if row.get("assignee", "—") == "—":
            unassigned += 1
    ordered = {p: by_priority.get(p, 0) for p in PRIORITY_ORDER + [UNRANKED]
               if by_priority.get(p)}
    return {"total": len(rows), "by_priority": ordered, "by_board": by_board,
            "unassigned": unassigned,
            "oldest_critical": next((r for r in rows
                                     if r["priority"] == "Critical"), None)}


# -- snapshots --------------------------------------------------------------
def snapshot(rows: list[dict] | None = None) -> dict:
    """Record today's state so tomorrow can be compared against it."""
    rows = collect() if rows is None else rows
    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": dt.date.today().isoformat(),
        "summary": summarise(rows),
        "keys": {r["key"]: r["priority"] for r in rows},
    }
    path = _snapshots_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
    LOG.info("boards.snapshot", tickets=entry["summary"]["total"])
    return entry


def load_snapshots() -> list[dict]:
    path = _snapshots_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def changes(rows: list[dict] | None = None) -> dict:
    """What moved since the last snapshot."""
    rows = collect() if rows is None else rows
    history = load_snapshots()
    if not history:
        return {"baseline": None, "note": "no previous snapshot to compare against"}
    previous = history[-1]
    before = previous["keys"]
    now = {r["key"]: r["priority"] for r in rows}
    raised = [{"key": k, "from": before[k], "to": now[k]} for k in now
              if k in before and priority_rank(now[k]) < priority_rank(before[k])]
    lowered = [{"key": k, "from": before[k], "to": now[k]} for k in now
               if k in before and priority_rank(now[k]) > priority_rank(before[k])]
    return {
        "baseline": previous["date"],
        "new": [k for k in now if k not in before],
        "closed": [k for k in before if k not in now],
        "raised": raised,
        "lowered": lowered,
    }


def render(rows: list[dict], top: int = 20) -> str:
    stats = summarise(rows)
    out = [f"# Board priorities — {dt.date.today().isoformat()}", ""]
    counts = " · ".join(f"{n} {p}" for p, n in stats["by_priority"].items())
    out += [f"{stats['total']} open across "
            f"{len(config.boards().get('tracked_boards') or [])} boards: {counts}",
            f"{stats['unassigned']} unassigned.", ""]
    for board, by_priority in stats["by_board"].items():
        line = ", ".join(f"{by_priority.get(p, 0)} {p}" for p in PRIORITY_ORDER
                         if by_priority.get(p))
        out.append(f"- **{board}**: {line or 'nothing open'}")
    out += ["", f"## Top {top}, highest priority and longest waiting first", ""]
    out += [f"| {'Priority':<9} | {'Ticket':<12} | {'Age':>4} | {'Assignee':<20} | Summary |",
            "|---|---|---|---|---|"]
    for row in rows[:top]:
        out.append(f"| {row['priority']:<9} | {row['key']:<12} | {row['age_days']:>3}d "
                   f"| {row['assignee'][:20]:<20} | {row['summary'][:60]} |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.chief_of_staff.boards")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_r = sub.add_parser("rank")
    p_r.add_argument("--top", type=int, default=20)
    p_r.add_argument("--json", action="store_true")
    sub.add_parser("snapshot")
    sub.add_parser("changes")
    args = ap.parse_args(argv)

    if args.cmd == "rank":
        rows = collect()
        print(json.dumps(rows[:args.top], indent=2) if args.json
              else render(rows, args.top))
    elif args.cmd == "snapshot":
        print(json.dumps(snapshot()["summary"], indent=2))
    else:
        print(json.dumps(changes(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
