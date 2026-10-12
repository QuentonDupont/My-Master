"""Research — fix plans and requester drafts for open intake. Every 15 minutes.

    open_research -> fetch_open_work -> plan_tickets -> build_outbox ->
    write_review -> flag_failed_tickets

Reads PESD1 tickets in Waiting Support / Blocked / Waiting for customer, with
their comments and linked PRDT work, and writes a research packet per ticket,
a requester draft outbox and an HTML report under review/work_agent/. Packets
are cached by fingerprint, so an unchanged ticket costs one Jira read.

No external writes, and the ledger is never touched: researching a ticket here
never reclaims or re-executes its proposal. Drafts are marked unsent — they are
for a human to send from wherever requester messages are handled.

Context in (optional): issues (skips the Jira fetch), sheet, sheet_path,
output, historian, analyst, analyst_kind.
"""
from __future__ import annotations

from core import config
from core.flow import LOCAL, READ, Every, Flow, Step, agent


def _wa():
    from agents.jira_leader import work_agent
    return work_agent


@agent("open_research", READ)
def open_research(ctx):
    """Analyst, corpus, intake sheet and output folder for this pass."""
    from agents.historian.retrieval import Historian
    from agents.jira_leader import analysis
    from core import requester

    if ctx.get("analyst") is None:
        ctx["analyst"] = analysis.get_analyst(ctx.get("analyst_kind") or "auto")
    if ctx.get("historian") is None:
        ctx["historian"], ctx["_own_hist"] = Historian(), True
    if ctx.get("sheet") is None:
        ctx["sheet"] = requester.load_sheet(ctx.get("sheet_path"))
    output = ctx.get("output") or (config.REVIEW_DIR / "work_agent")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    ctx["output"] = output


@agent("fetch_open_work", READ)
def fetch_open_work(ctx):
    """Open intake tickets, their comments and linked PRDT tickets."""
    if ctx.get("issues") is None:
        ctx["issues"] = _wa().fetch_open_work()


@agent("plan_tickets", LOCAL)
def plan_tickets(ctx):
    """A research packet per ticket (cached while the inputs are unchanged)."""
    ctx["packets"], ctx["errors"] = _wa().plan_tickets(
        ctx["issues"], sheet=ctx["sheet"], output=ctx["output"],
        historian=ctx["historian"], analyst=ctx["analyst"])


@agent("build_outbox", LOCAL)
def build_outbox(ctx):
    """Group verified-requester drafts into one unsent message per person."""
    ctx["outbox"] = _wa().build_outbox(ctx["packets"])


@agent("write_review", LOCAL)
def write_review(ctx):
    """Write outbox.json, summary.json and index.html."""
    ctx["report"] = _wa().write_review(ctx["output"], ctx["packets"], ctx["errors"],
                                       ctx["outbox"], ctx["sheet"], ctx["analyst"].name)


@agent("flag_failed_tickets", LOCAL)
def flag_failed_tickets(ctx):
    """Fail the run (and so alert) when any ticket could not be researched."""
    if ctx["errors"]:
        raise RuntimeError(f"{len(ctx['errors'])} ticket(s) failed: "
                           + ", ".join(e["ticket"] for e in ctx["errors"]))


def _close(ctx) -> None:
    if ctx.get("_own_hist"):
        ctx["historian"].close()


RESEARCH = Flow(
    "research",
    [open_research, fetch_open_work, plan_tickets, build_outbox, write_review,
     Step(flag_failed_tickets, on_error="continue")],
    triggers=[Every(900)],
    doc="Research open intake: fix plans and unsent requester drafts in review/work_agent/.",
    cleanup=_close,
    timeout_seconds=1800,
)

FLOWS = (RESEARCH,)
