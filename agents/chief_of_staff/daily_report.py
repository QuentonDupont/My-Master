"""The 9am status update: one report per team, health checks, and a short
yesterday/today, emailed to the board owner.

    python -m agents.chief_of_staff.daily_report run              # print only
    python -m agents.chief_of_staff.daily_report run --send        # actually email
    python -m agents.chief_of_staff.daily_report run --send --to x@y.com

Reads only. Building this section touches no ledger state and sends nothing
by default — `--send` is the explicit opt-in the rest of the repo uses for any
write, and emailing someone is a write.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from core import config, ledger as ledger_mod, log, mailer, proposals
from agents.chief_of_staff import boards as boards_mod
from agents.chief_of_staff import brief as brief_mod
from agents.chief_of_staff import health as health_mod

LOG = log.get("chief_of_staff")

TEAMS = ("Jira Leader", "Historian", "Chief of Staff", "Marketing & Onsite")


def _yesterday_window() -> tuple[dt.datetime, dt.datetime]:
    now = dt.datetime.utcnow()
    return now - dt.timedelta(hours=24), now


def _executed_since(since: dt.datetime) -> list[dict]:
    with ledger_mod.Ledger() as led:
        rows = led.by_state(ledger_mod.EXECUTED)
    out = []
    for r in rows:
        try:
            ts = dt.datetime.fromisoformat(r["last_processed"]).replace(tzinfo=None)
        except (KeyError, ValueError, TypeError):
            continue
        if ts >= since:
            out.append(r)
    return out


def _content_tasks_since(since: dt.datetime) -> list:
    from agents.marketing_onsite import tasks as mo_tasks
    out = []
    for t in mo_tasks.all_tasks():
        try:
            ts = dt.datetime.fromisoformat(t.created)
            ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
        except (ValueError, TypeError):
            continue
        if ts >= since:
            out.append(t)
    return out


def gather() -> dict:
    since, now = _yesterday_window()
    b = brief_mod.brief()
    h = health_mod.check_all()

    executed_24h = _executed_since(since)
    content_24h = _content_tasks_since(since)

    from agents.marketing_onsite import lead as mo_lead
    mo_report_text = mo_lead.report()

    return {
        "generated": now.isoformat(timespec="minutes") + "Z",
        "health": h,
        "jira_leader": {
            "waiting_for_decision": len(b["waiting_for_you"]),
            "approved_not_executed": len(b["approved_not_yet_executed"]),
            "escalations": len(b["escalations"]),
            "executed_last_24h": [
                {"ticket": r["ticket_key"], "clone": r.get("clone_key")}
                for r in executed_24h],
            "boards": b.get("boards", {}),
        },
        "historian": h["historian"],
        "chief_of_staff": {
            "corrections_logged": b["corrections_logged"],
            "rule_proposals_pending": len(b["rule_proposals"]),
            "top_rule_proposals": b["rule_proposals"][:3],
            "cost_7d": b["cost_7d"],
        },
        "marketing_onsite": {
            "health": h["marketing_onsite"],
            "content_tasks_last_24h": len(content_24h),
            "report_text": mo_report_text,
        },
        "errors": b["errors"],
    }


def _team_line(name: str, ok: bool, detail: str) -> str:
    mark = "OK" if ok else "!!"
    return f"[{mark}] {name} — {detail}"


def render(data: dict) -> str:
    out = [f"Pomelo triage farm — daily status, {data['generated'][:16].replace('T', ' ')}",
           "=" * 60, ""]

    # -- health, one line per team, worst news first ------------------------
    out.append("HEALTH")
    hj = data["jira_leader"]
    jira_ok = hj["escalations"] == 0 and not data["health"]["jira_leader"].get("error")
    out.append(_team_line(
        "Jira Leader", jira_ok,
        f"{hj['waiting_for_decision']} waiting, {hj['approved_not_executed']} "
        f"approved not executed, {hj['escalations']} escalations"))

    hist = data["historian"]
    hist_ok = not hist.get("error") and not hist.get("stale")
    out.append(_team_line(
        "Historian", hist_ok,
        f"{hist.get('documents', '?')} documents indexed, "
        f"{hist.get('index_age_hours', '?')}h since last export"
        if not hist.get("error") else hist["error"]))

    cos = data["chief_of_staff"]
    out.append(_team_line(
        "Chief of Staff", True,
        f"{cos['corrections_logged']} corrections logged, "
        f"{cos['rule_proposals_pending']} rule proposals pending your approval"))

    mo = data["marketing_onsite"]
    mo_by_state = mo["health"].get("by_state", {})
    mo_blocked = mo_by_state.get("NEEDS_INPUT", 0)
    mo_ok = not mo["health"].get("error")
    out.append(_team_line(
        "Marketing & Onsite", mo_ok,
        f"{mo['health'].get('total', 0)} tasks total, {mo_blocked} waiting on you, "
        f"{mo['content_tasks_last_24h']} new in the last day"))

    ld = data["health"]["launchd"]
    down = [label for label, s in ld.items() if s.get("status") not in
           ("running", "loaded, not running")]
    out.append("")
    out.append("INFRA  " + ("all launchd jobs answering"
                            if not down else f"NOT RUNNING: {', '.join(down)}"))
    out.append("")

    # -- yesterday ------------------------------------------------------
    out.append("YESTERDAY")
    ex = hj["executed_last_24h"]
    if ex:
        out.append(f"- {len(ex)} PESD1 ticket(s) executed: "
                   + ", ".join(e["ticket"] for e in ex[:10])
                   + (" …" if len(ex) > 10 else ""))
    else:
        out.append("- nothing executed in the last 24h")
    if mo["content_tasks_last_24h"]:
        out.append(f"- {mo['content_tasks_last_24h']} content task(s) came in for "
                   f"Marketing & Onsite")
    errs = data["errors"]
    if errs["total_errors"]:
        out.append(f"- {errs['total_errors']} error(s) logged across the farm "
                   f"(see below)")
    out.append("")

    # -- today ------------------------------------------------------------
    out.append("TODAY")
    if hj["waiting_for_decision"]:
        out.append(f"- {hj['waiting_for_decision']} PESD1 proposal(s) waiting for "
                   f"your decision")
    if hj["escalations"]:
        out.append(f"- {hj['escalations']} escalation(s) need you, not the system")
    if hj["approved_not_executed"]:
        out.append(f"- {hj['approved_not_executed']} approved proposal(s) not yet "
                   f"executed")
    if mo_blocked:
        out.append(f"- {mo_blocked} content task(s) in Marketing & Onsite waiting "
                   f"on input from you")
    if cos["rule_proposals_pending"]:
        out.append(f"- {cos['rule_proposals_pending']} rule proposal(s) ready for "
                   f"your approval")
    if not any([hj["waiting_for_decision"], hj["escalations"],
               hj["approved_not_executed"], mo_blocked,
               cos["rule_proposals_pending"]]):
        out.append("- queue is empty; nothing needs you right now")
    out.append("")

    # -- Marketing & Onsite detail -----------------------------------------
    out.append("MARKETING & ONSITE — full queue")
    out.append("-" * 60)
    out.append(mo["report_text"])
    out.append("")

    if errs["log_errors"] or errs["warn_by_logger"]:
        out.append("ERRORS / WARNINGS (7 days)")
        for e in errs["log_errors"][-10:]:
            out.append(f"- {e['ts']}  {e['logger']}  {e['event']}")
        if errs["warn_by_logger"]:
            top = ", ".join(f"{n} ({c})" for n, c in
                            list(errs["warn_by_logger"].items())[:5])
            out.append(f"- warnings: {top}")
        out.append("")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.chief_of_staff.daily_report")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--send", action="store_true",
                   help="actually email it; default is print-only")
    r.add_argument("--to", default="", help="override REPORT_EMAIL_TO")
    r.add_argument("--json", action="store_true", help="print the raw data, not text")
    args = ap.parse_args(argv)

    data = gather()
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    text = render(data)
    print(text)

    subject = f"Pomelo triage — daily status, {dt.date.today().isoformat()}"
    result = mailer.send(subject, text, to=args.to, dry_run=not args.send)
    if args.send:
        print(f"\nsent to {result['to']}")
    else:
        print(f"\n(dry run — pass --send to actually email {result['to']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
