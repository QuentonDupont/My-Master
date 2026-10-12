"""Retriage — re-run triage on one ticket by hand, because a rule changed.

Human only, and deliberately outside invariant 4's re-processing gate. That
gate exists to stop the standing sweep looping on its own output; it has
nothing to say about a ticket whose content never changed but whose
*handling* should, because the rules themselves just changed. That is
exactly what happened on 12 Oct 2026: the Crashlytics fixed route and six
return-ticket corrections, each one fixed that day with a one-off script
because this flow did not exist yet.

    claim_for_retriage -> run_triage (the normal triage sub-flow, reusing
    the ticket's existing proposal_id so the review batch shows one
    corrected entry, never an orphan) -> record_retriage_correction

Refuses a ticket whose ledger state is NEW, CLAIMED, APPROVED, CORRECTED,
EXECUTED or ROLLED_BACK — those need the ordinary flow (the next sweep,
approve/execute, or undo), not a silent re-triage. Works on PROPOSED,
ESCALATED, DUPLICATE and REJECTED: a ticket with a stored opinion that
might now be wrong.

    python -m flows run retriage --confirm \
        --data '{"ticket": "PESD1-11337", "reason": "..."}'

Context in: ticket (required), reason (required — becomes the correction's
            reason, same as any other human-taught correction), and
            optionally issue (skip the live Jira fetch, for tests),
            analyst_kind, sheet_path.
"""
from __future__ import annotations

from core import ledger as ledger_mod, log, proposals
from core.flow import LOCAL, READ, Flow, Human, agent
from flows.triage import TRIAGE

LOG = log.get("retriage")

#: ledger states a ticket may be re-triaged from. Anything else needs the
#: ordinary flow, not this one -- see the module docstring.
RETRIAGEABLE = (ledger_mod.PROPOSED, ledger_mod.ESCALATED, ledger_mod.DUPLICATE,
                ledger_mod.REJECTED)


@agent("load_for_retriage", READ)
def load_for_retriage(ctx):
    """The live ticket, its ledger row, and its current stored proposal."""
    from agents.jira_leader.sources import KeyTicketSource

    if not ctx.get("ticket"):
        raise ledger_mod.LedgerError("retriage needs a ticket key")
    if not ctx.get("reason"):
        raise ledger_mod.LedgerError(
            "retriage needs a reason -- it becomes the correction's reason, "
            "same as any other taught correction")
    key = ctx["ticket"]
    if ctx.get("issue") is None:
        ctx["issue"] = KeyTicketSource([key]).get(key)

    ctx["led"], ctx["_own_led"] = ledger_mod.Ledger(), True
    row = ctx["led"].get(key)
    if row is None:
        raise ledger_mod.LedgerError(f"{key} is not in the ledger -- nothing to re-triage")
    if row["state"] not in RETRIAGEABLE:
        raise ledger_mod.LedgerError(
            f"{key} is {row['state']}, not one of {RETRIAGEABLE} -- re-triage is "
            f"only for a ticket with a stored opinion that might be wrong, not "
            f"one already decided, in flight, or executed")
    ctx["reuse_proposal_id"] = row["proposal_id"]
    ctx["before_proposal"] = proposals.load(row["proposal_id"]).to_dict()


@agent("claim_for_retriage", LOCAL)
def claim_for_retriage(ctx):
    """Re-claim the ticket so triage can run again. Legal from every state in
    RETRIAGEABLE -- PROPOSED -> CLAIMED is the one edge this flow needed
    added to core/ledger.py; the other three already existed for the same
    reason (a human re-opening a decided ticket)."""
    ctx["led"].transition(ctx["ticket"], ledger_mod.CLAIMED)


@agent("run_triage", LOCAL)
def run_triage(ctx):
    """The normal triage sub-flow, told to keep the existing proposal_id."""
    result = TRIAGE.run(issue=ctx["issue"], led=ctx["led"],
                        analyst=ctx.get("analyst"), sheet=ctx.get("sheet"),
                        rules=ctx.get("rules"), reuse_proposal_id=ctx["reuse_proposal_id"])
    result.raise_for_error()
    ctx["outcome"] = result.data.get("outcome")


@agent("record_retriage_correction", LOCAL)
def record_retriage_correction(ctx):
    """Diff the old proposal against the new one and log it as a correction --
    the same learning-loop record a human editing a proposal by hand leaves."""
    from core import corrections

    after = proposals.load(ctx["reuse_proposal_id"])
    changes = proposals.diff(ctx["before_proposal"], after.to_dict())
    if changes:
        corrections.record_many(ctx["reuse_proposal_id"], changes, ctx["reason"])
    LOG.info("retriage.corrected", ticket=ctx["ticket"], proposal=ctx["reuse_proposal_id"],
             changes=len(changes), outcome=ctx["outcome"])
    ctx["report"] = {"ticket": ctx["ticket"], "proposal_id": ctx["reuse_proposal_id"],
                     "outcome": ctx["outcome"], "changes": len(changes)}


def _close(ctx) -> None:
    if ctx.get("_own_led"):
        ctx["led"].close()


RETRIAGE = Flow(
    "retriage",
    [load_for_retriage, claim_for_retriage, run_triage, record_retriage_correction],
    triggers=[Human()],
    doc="Re-run triage on one ticket by hand, because a rule changed, not the ticket.",
    cleanup=_close,
)

FLOWS = (RETRIAGE,)
