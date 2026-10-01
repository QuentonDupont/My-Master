"""Chief of Staff — morning brief, cost, error review.

Reads only. It never changes ledger state, never writes to Jira, and never edits
knowledge/rules.md (it proposes; see agents/chief_of_staff/rules.py).

    python -m agents.chief_of_staff.brief brief
    python -m agents.chief_of_staff.brief cost --days 7
    python -m agents.chief_of_staff.brief errors
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from agents.chief_of_staff import boards as boards_mod
from agents.chief_of_staff import rules as rules_mod
from core import config, corrections, ledger as ledger_mod, log, proposals

LOG = log.get("chief_of_staff")

#: USD per million tokens, from the Claude API pricing table.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def _read_log(name: str, since: dt.datetime | None = None) -> list[dict]:
    path = config.LOG_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since:
            try:
                ts = dt.datetime.fromisoformat(record["ts"]).replace(tzinfo=None)
            except (KeyError, ValueError):
                ts = None
            if ts and ts < since:
                continue
        out.append(record)
    return out


def cost(days: int = 7) -> dict:
    """What the analyst has spent. Zero when running on the heuristic analyst."""
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    calls = [r for r in _read_log("analysis", since) if r.get("event") == "analysis.claude"]
    by_model: dict[str, dict] = {}
    for call in calls:
        model = call.get("model", "unknown")
        entry = by_model.setdefault(model, {"calls": 0, "input_tokens": 0,
                                            "output_tokens": 0, "usd": 0.0})
        entry["calls"] += 1
        entry["input_tokens"] += int(call.get("input_tokens") or 0)
        entry["output_tokens"] += int(call.get("output_tokens") or 0)
    for model, entry in by_model.items():
        in_price, out_price = PRICES.get(model, (0.0, 0.0))
        entry["usd"] = round(entry["input_tokens"] / 1e6 * in_price
                             + entry["output_tokens"] / 1e6 * out_price, 4)
    total = round(sum(e["usd"] for e in by_model.values()), 4)
    fallbacks = len([r for r in _read_log("analysis", since)
                     if r.get("event") == "analysis.claude_failed"])
    return {"days": days, "by_model": by_model, "total_usd": total,
            "analyst_fallbacks": fallbacks,
            "note": "0 when the heuristic analyst is in use — it costs nothing."}


def _all_logger_names() -> list[str]:
    """Every logger that has actually written a file, not a hand-kept list.

    A hardcoded tuple here drifts the moment a new module calls log.get(...) —
    it did: brief.errors() scanned 5 loggers while 29 exist, so failures in
    jira_leader (where the queue's own errors land), mcp, requester
    and everything else were never counted in the one place meant to surface
    them.
    """
    if not config.LOG_DIR.is_dir():
        return []
    return sorted(p.stem for p in config.LOG_DIR.glob("*.jsonl"))


def errors(days: int = 7) -> dict:
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    with ledger_mod.Ledger() as led:
        stuck = [r for r in led.by_state() if r["last_error"]]
        retried = [r for r in led.by_state() if (r["attempts"] or 0) > 1]
    log_errors = []
    warn_by_logger: dict[str, int] = {}
    for name in _all_logger_names():
        records = _read_log(name, since)
        log_errors += [r for r in records if r.get("level") == "ERROR"]
        warns = sum(1 for r in records if r.get("level") == "WARN")
        if warns:
            warn_by_logger[name] = warns
    log_errors.sort(key=lambda r: r.get("ts", ""))
    return {
        "tickets_with_last_error": [
            {"ticket": r["ticket_key"], "state": r["state"], "attempts": r["attempts"],
             "error": r["last_error"]} for r in stuck],
        "retried_tickets": [r["ticket_key"] for r in retried],
        "log_errors": [{"ts": r["ts"], "logger": r["logger"], "event": r["event"]}
                       for r in log_errors[-20:]],
        "total_errors": len(log_errors),
        # WARN is never fatal but a spike is a sign something is degrading
        # silently (a token nearing expiry, a rate limit) — surfaced as counts
        # per logger rather than every line, or this drowns the brief.
        "warn_by_logger": dict(sorted(warn_by_logger.items(), key=lambda kv: -kv[1])),
    }


def brief() -> dict:
    with ledger_mod.Ledger() as led:
        stats = led.stats()
        waiting = led.by_state(ledger_mod.PROPOSED)
        approved = led.by_state(ledger_mod.APPROVED, ledger_mod.CORRECTED)
        escalated = led.by_state(ledger_mod.ESCALATED)
        executed = led.by_state(ledger_mod.EXECUTED)

    def summarise(rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            item = {"ticket": row["ticket_key"], "proposal": row["proposal_id"]}
            try:
                p = proposals.load(row["proposal_id"]) if row["proposal_id"] else None
            except FileNotFoundError:
                p = None
            if p:
                item.update({"classification": p.classification,
                             "confidence": p.confidence,
                             "requirement": p.requirement_restated,
                             "flags": p.flags})
            out.append(item)
        return out

    latest_batch = sorted(config.REVIEW_DIR.glob("batch_*.md"))[-1:] or [None]
    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "ledger": stats,
        "waiting_for_you": summarise(waiting),
        "approved_not_yet_executed": summarise(approved),
        "escalations": summarise(escalated),
        "executed_total": len(executed),
        "latest_batch": str(latest_batch[0]) if latest_batch[0] else None,
        "corrections_logged": len(corrections.load()),
        "rule_proposals": rules_mod.cluster(),
        "cost_7d": cost(7),
        "errors": errors(7),
        "boards": _board_section(),
    }


def _board_section() -> dict:
    """Tracked boards, ranked. Never lets a board outage break the brief."""
    try:
        rows = boards_mod.collect()
    except Exception as exc:  # pragma: no cover - network path
        LOG.warn("brief.boards_unavailable", error=str(exc)[:200])
        return {"error": str(exc)[:200]}
    return {"summary": boards_mod.summarise(rows),
            "top": rows[:8],
            "changes": boards_mod.changes(rows)}


def render(data: dict) -> str:
    out = [f"# Morning brief — {data['generated'][:16].replace('T', ' ')}", ""]
    waiting = data["waiting_for_you"]
    out += [f"**{len(waiting)} proposals waiting for you**, "
            f"{len(data['approved_not_yet_executed'])} approved but not executed, "
            f"{len(data['escalations'])} escalations, "
            f"{data['executed_total']} executed to date.", ""]
    if data["latest_batch"]:
        out += [f"Latest batch: `{data['latest_batch']}`", ""]

    if waiting:
        out += ["## Waiting for your decision", ""]
        for item in sorted(waiting, key=lambda i: -(i.get("confidence") or 0)):
            out.append(f"- **{item['ticket']}** ({item.get('classification')}, "
                       f"{item.get('confidence', 0):.2f}) — {item.get('requirement', '')}"
                       + (f"  ⚑ {', '.join(item['flags'])}" if item.get("flags") else ""))
        out.append("")
    if data["escalations"]:
        out += ["## Escalations — these need you, not the system", ""]
        for item in data["escalations"]:
            out.append(f"- **{item['ticket']}** — {item.get('requirement', '')}"
                       + (f"  ⚑ {', '.join(item['flags'])}" if item.get("flags") else ""))
        out.append("")
    if data["approved_not_yet_executed"]:
        out += ["## Approved, awaiting execution", ""]
        out += [f"- {i['ticket']} (`{i['proposal']}`)"
                for i in data["approved_not_yet_executed"]]
        out += ["", "Run: `python -m agents.jira_leader.batch execute --execute`", ""]

    rule_proposals = data["rule_proposals"]
    out += ["## Learning loop", "",
            f"- {data['corrections_logged']} corrections logged",
            f"- {len(rule_proposals)} rule proposals ready for your approval"]
    for rule in rule_proposals[:5]:
        out.append(f"  - ({rule['support']}×) {rule['rule']}")
    out.append("")

    cost_data = data["cost_7d"]
    out += ["## Cost (7 days)", "",
            f"- ${cost_data['total_usd']:.2f} across "
            f"{sum(m['calls'] for m in cost_data['by_model'].values())} analyst calls"]
    if cost_data["analyst_fallbacks"]:
        out.append(f"- ⚠ {cost_data['analyst_fallbacks']} analyst calls fell back to "
                   f"the heuristic")
    out.append("")

    board = data.get("boards") or {}
    if board.get("summary"):
        stats = board["summary"]
        counts = " · ".join(f"{n} {p}" for p, n in stats["by_priority"].items())
        out += ["## Boards", "", f"{stats['total']} open — {counts}."]
        change = board.get("changes") or {}
        if change.get("baseline"):
            bits = []
            if change.get("new"):
                bits.append(f"{len(change['new'])} new")
            if change.get("closed"):
                bits.append(f"{len(change['closed'])} closed")
            if change.get("raised"):
                bits.append(f"{len(change['raised'])} raised in priority")
            if bits:
                out.append(f"Since {change['baseline']}: " + ", ".join(bits) + ".")
        out.append("")
        for row in board.get("top", []):
            out.append(f"- **{row['priority']}** {row['key']} ({row['age_days']}d, "
                       f"{row['assignee']}) — {row['summary'][:64]}")
        out.append("")

    errs = data["errors"]
    if errs["tickets_with_last_error"] or errs["log_errors"] or errs["warn_by_logger"]:
        out += ["## Errors", ""]
        for item in errs["tickets_with_last_error"]:
            out.append(f"- {item['ticket']} ({item['state']}, {item['attempts']} attempts) "
                       f"— {item['error']}")
        if errs["log_errors"]:
            out.append(f"- {errs['total_errors']} error log entries in the last 7 days "
                       f"(across {config.LOG_DIR})")
        if errs["warn_by_logger"]:
            top = ", ".join(f"{n} ({c})" for n, c in
                            list(errs["warn_by_logger"].items())[:5])
            out.append(f"- warnings piling up in: {top}")
        out.append("")
    else:
        out += ["## Errors", "", "None.", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.chief_of_staff.brief")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_b = sub.add_parser("brief")
    p_b.add_argument("--json", action="store_true")
    p_b.add_argument("--write", action="store_true", help="also save into review/")
    p_c = sub.add_parser("cost")
    p_c.add_argument("--days", type=int, default=7)
    p_e = sub.add_parser("errors")
    p_e.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)

    if args.cmd == "brief":
        data = brief()
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            text = render(data)
            print(text)
            if args.write:
                path = (config.REVIEW_DIR /
                        f"brief_{dt.datetime.now():%Y-%m-%d_%H%M}.md")
                Path(path).write_text(text, encoding="utf-8")
                print(f"\nwritten to {path}")
    elif args.cmd == "cost":
        print(json.dumps(cost(args.days), indent=2))
    else:
        print(json.dumps(errors(args.days), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
