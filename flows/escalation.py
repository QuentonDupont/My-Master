"""Escalation — the board owner answers a ticket the system escalated.
Human only: it carries the human's own words.

    check_escalation -> rewrite_proposal -> record_correction -> requeue_ticket

The answer becomes the proposed comment (ANSWERABLE, confidence 1.0, flagged
human_answer), the change is logged as a correction, and the ticket goes back
to PROPOSED — where it is approved and executed like any other proposal.
Nothing here writes to Jira.

Refused (ReentryRefused): empty answers, tickets not ESCALATED, and never-touch
tickets — those are answered in Jira by the board owner under their own name.

    python -m flows run escalation --confirm \
        --data '{"proposal_id": "p_0142", "comment": "..."}'

Context in: proposal_id, comment, and optionally reason, led.
"""
from __future__ import annotations

from core import ledger as ledger_mod, log, proposals
from core.flow import LOCAL, READ, Flow, Human, agent

LOG = log.get("reentry")


def _r():
    from agents.jira_leader import reentry
    return reentry


@agent("check_escalation", READ)
def check_escalation(ctx):
    """Only a non-empty answer, to an ESCALATED ticket that is not never-touch."""
    r = _r()
    if not ctx.get("proposal_id"):
        raise r.ReentryRefused("which proposal? proposal_id is required")
    comment = (ctx.get("comment") or "").strip()
    if not comment:
        raise r.ReentryRefused("an answer with no text is not an answer")
    ctx["comment"] = comment
    if ctx.get("led") is None:
        ctx["led"], ctx["_own_led"] = ledger_mod.Ledger(), True
    proposal = proposals.load(ctx["proposal_id"])
    row = ctx["led"].get(proposal.ticket)
    if row is None:
        raise r.ReentryRefused(f"{proposal.ticket} is not in the ledger")
    if row["state"] != ledger_mod.ESCALATED:
        raise r.ReentryRefused(
            f"{proposal.ticket} is {row['state']}, not {ledger_mod.ESCALATED}; "
            "re-entry is only for a ticket the system handed to a human")
    blocked = [f for f in proposal.flags
               if f == r.NEVER_TOUCH_FLAG or f.startswith(f"{r.NEVER_TOUCH_FLAG}:")]
    if blocked:
        raise r.ReentryRefused(
            f"{proposal.ticket} is never-touch ({', '.join(blocked)}); "
            "answer it in Jira yourself, under your own name")
    ctx["proposal"] = proposal


@agent("rewrite_proposal", LOCAL)
def rewrite_proposal(ctx):
    """The human's answer becomes the comment; no clone, no transition."""
    r = _r()
    proposal = ctx["proposal"]
    ctx["before"] = proposal.to_dict()
    proposal.classification = proposals.ANSWERABLE
    proposal.proposed_comment = ctx["comment"]
    proposal.clone = None
    proposal.pesd1_transition = None
    proposal.confidence = 1.0          # a person wrote it; it is not a guess
    proposal.flags = [f for f in proposal.flags if f != "sanity_gate"]
    if r.HUMAN_FLAG not in proposal.flags:
        proposal.flags.append(r.HUMAN_FLAG)
    problems = proposals.validate(proposal.to_dict())
    if problems:
        raise r.ReentryRefused(f"the answered proposal is invalid: {problems}")


@agent("record_correction", LOCAL)
def record_correction(ctx):
    """Log what the human changed, for the weekly rules flow to learn from."""
    from core import corrections

    corrections.record_many(
        ctx["proposal_id"], proposals.diff(ctx["before"], ctx["proposal"].to_dict()),
        ctx.get("reason") or "answered by the board owner after escalation")


@agent("requeue_ticket", LOCAL)
def requeue_ticket(ctx):
    """Save the proposal and move the ticket ESCALATED -> CLAIMED -> PROPOSED."""
    proposal, led = ctx["proposal"], ctx["led"]
    proposals.save(proposal)
    led.transition(proposal.ticket, ledger_mod.CLAIMED)
    led.transition(proposal.ticket, ledger_mod.PROPOSED, proposal_id=ctx["proposal_id"])
    LOG.info("reentry.answered", ticket=proposal.ticket, proposal=ctx["proposal_id"],
             chars=len(ctx["comment"]))
    ctx["report"] = {"proposal_id": ctx["proposal_id"], "ticket": proposal.ticket,
                     "state": ledger_mod.PROPOSED, "chars": len(ctx["comment"])}


def _close(ctx) -> None:
    if ctx.get("_own_led"):
        ctx["led"].close()


ESCALATION = Flow(
    "escalation",
    [check_escalation, rewrite_proposal, record_correction, requeue_ticket],
    triggers=[Human()],
    doc="Turn the board owner's answer to an escalation into a reviewable proposal.",
    cleanup=_close,
)

FLOWS = (ESCALATION,)
