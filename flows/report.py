"""Reporting and learning — what the board owner reads, and what the system
proposes to learn.

daily-report  daily at 09:00
    check_health -> build_brief -> assemble_report -> render_report ->
    save_report -> send_report

    Health of every flow and job, the queue waiting on the board owner, what was
    executed yesterday, the Marketing & Onsite queue, and errors. Saved to
    review/daily_reports/<date>.md every run. Emailed as well only when Gmail
    sending is configured in .env — since 12 Oct 2026 it is not, by choice, and
    send_report skips rather than fails.

rules         Mondays at 08:30
    cluster_corrections -> write_rule_proposals

    Clusters knowledge/corrections.jsonl into proposed rules and writes them to
    review/rule_proposals_<ts>.md. Proposals only: a rule takes effect when the
    human copies it into knowledge/rules.md.

Context in (optional): to (override recipient).
"""
from __future__ import annotations

import datetime as dt

from core.flow import EMAIL, LOCAL, READ, Daily, Flow, agent


@agent("check_health", READ)
def check_health(ctx):
    """Flows, launchd jobs, ledger, corpus and content-task health."""
    from agents.chief_of_staff import health

    ctx["health"] = health.check_all()


@agent("build_brief", READ)
def build_brief(ctx):
    """The queue: waiting for a decision, approved not executed, escalations."""
    from agents.chief_of_staff import brief

    ctx["brief"] = brief.brief()


@agent("assemble_report", READ)
def assemble_report(ctx):
    """Combine brief and health with yesterday's executions and content tasks."""
    from agents.chief_of_staff import daily_report

    ctx["report_data"] = daily_report.assemble(ctx["brief"], ctx["health"])


@agent("render_report", LOCAL)
def render_report(ctx):
    """Plain-text report, worst news first."""
    from agents.chief_of_staff import daily_report

    ctx["report"] = daily_report.render(ctx["report_data"])


@agent("save_report", LOCAL)
def save_report(ctx):
    """Write the report to review/daily_reports/<date>.md — where it is read."""
    from core import config

    out = config.REVIEW_DIR / "daily_reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{dt.date.today().isoformat()}.md"
    path.write_text(ctx["report"] + "\n", encoding="utf-8")
    ctx["saved"] = str(path)


@agent("send_report", EMAIL)
def send_report(ctx):
    """Email the report too, only if Gmail sending is configured."""
    from core import mailer

    if not mailer.configured():
        # The board owner chose a file over an email (12 Oct 2026). Without
        # Gmail credentials this is a deliberate skip, not a failure.
        ctx["sent"] = {"skipped": "email not configured; report saved to file"}
        return
    subject = f"Pomelo triage — daily status, {dt.date.today().isoformat()}"
    ctx["sent"] = mailer.send(subject, ctx["report"], to=ctx.get("to") or "",
                              dry_run=not ctx.execute)


DAILY_REPORT = Flow(
    "daily-report",
    [check_health, build_brief, assemble_report, render_report, save_report,
     send_report],
    triggers=[Daily("09:00")],
    doc="Morning status report to review/daily_reports/ (emailed only if Gmail is set up).",
    live_when_scheduled=True,
    timeout_seconds=900,
)


@agent("cluster_corrections", READ)
def cluster_corrections(ctx):
    """Group the human's corrections into candidate rules."""
    from agents.chief_of_staff import rules

    ctx["proposals"] = rules.cluster()


@agent("write_rule_proposals", LOCAL)
def write_rule_proposals(ctx):
    """Write the candidates to review/ for the human to approve (or not)."""
    from agents.chief_of_staff import rules

    if not ctx["proposals"]:
        ctx["report"] = "no rule proposals this week"
        return
    ctx["report"] = rules.write_proposal_file(ctx["proposals"])


RULES = Flow(
    "rules",
    [cluster_corrections, write_rule_proposals],
    triggers=[Daily("08:30", weekdays=(0,))],
    doc="Weekly: cluster corrections into rule proposals for human approval.",
    timeout_seconds=600,
)

FLOWS = (DAILY_REPORT, RULES)
