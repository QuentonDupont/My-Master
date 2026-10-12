"""Decisions — pick up approvals made in Jira. Every 5 minutes, and on every
new proposal.

    read_decision_labels -> apply_decisions -> report_decisions

A `triage-approved` / `triage-rejected` label on a PESD1 ticket is the human's
decision. This reads the labels (and, for a rejection, the newest comment as
the reason) and records them: ledger PROPOSED -> APPROVED / REJECTED, with the
correction logged. It writes nothing to Jira, and approving does not execute —
the execute flow stays a separate, human-confirmed step.

Recording is local, so scheduled runs apply for real; a manual run is a
dry run unless --execute.

Context in (optional): reader (a Jira read client), led.
"""
from __future__ import annotations

from core import log
from core.flow import LOCAL, READ, Every, Flow, On, agent

LOG = log.get("labels")


def _reader(ctx):
    if ctx.get("reader") is None:
        from core.jira_client import JiraReadClient
        ctx["reader"] = JiraReadClient()
    return ctx["reader"]


@agent("read_decision_labels", READ)
def read_decision_labels(ctx):
    """Find PROPOSED tickets carrying an approve or reject label."""
    from agents.jira_leader import labels

    found = labels.pending(_reader(ctx), ctx.get("led"))
    ctx["found"] = found
    ctx["decisions"] = [d for d in found if d["decision"] in ("approve", "reject")]
    ctx["conflicts"] = [d for d in found if d["decision"] == "conflict"]


@agent("apply_decisions", LOCAL)
def apply_decisions(ctx):
    """Record each decision in the ledger and the corrections log."""
    from agents.jira_leader import mobile

    ctx["applied"] = None
    if ctx.execute and ctx["decisions"]:
        ctx["applied"] = mobile.apply(
            [{"proposal_id": d["proposal_id"], "decision": d["decision"],
              "note": d["note"]} for d in ctx["decisions"]], led=ctx.get("led"))


@agent("report_decisions", LOCAL)
def report_decisions(ctx):
    """Summarise; a ticket with both labels is reported, never applied."""
    LOG.info("labels.poll", found=len(ctx["found"]), conflicts=len(ctx["conflicts"]),
             applied=bool(ctx.execute and ctx["decisions"]))
    if ctx["conflicts"]:
        LOG.warn("labels.conflicts", tickets=[c.get("ticket") for c in ctx["conflicts"]])
    ctx["report"] = {"found": len(ctx["found"]), "conflicts": ctx["conflicts"],
                     "decisions": ctx["decisions"], "applied": ctx["applied"]}


DECISIONS = Flow(
    "decisions",
    [read_decision_labels, apply_decisions, report_decisions],
    triggers=[Every(300), On("ticket.proposed")],
    doc="Read triage-approved / triage-rejected labels and record the decisions.",
    live_when_scheduled=True,
    timeout_seconds=600,
)

FLOWS = (DECISIONS,)
