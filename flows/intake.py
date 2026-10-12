"""Intake — sweep the PESD1 board and triage what is new. Every 5 minutes.

    choose_analyst -> poll_intake -> reclaim_stale_claims -> claim_tickets ->
    for each claimed ticket (5 at a time): triage -> summarise_intake

No Jira writes. The ledger's re-processing gate (invariant 4) decides what is
claimed; each claimed ticket gets its own triage sub-flow with its own ledger
and corpus connections.

Context in (all optional): source (a TicketSource; default live PESD1),
limit, max_workers, analyst_kind, sheet_path.
"""
from __future__ import annotations

from core import ledger as ledger_mod, log
from core.flow import LOCAL, READ, Every, Flow, ForEach, agent
from flows.triage import TRIAGE

LOG = log.get("jira_leader")

MAX_WORKERS = 5


def hash_of(issue: dict) -> str:
    from agents.jira_leader.sources import comment_count

    f = issue.get("fields", {}) or {}
    return ledger_mod.content_hash(f.get("summary") or "", f.get("description") or "",
                                   comment_count(issue))


@agent("choose_analyst", READ)
def choose_analyst(ctx):
    """Pick the analyst and load the sheet and rules — before touching the board."""
    from agents.jira_leader import analysis
    from core import requester

    kind = ctx.get("analyst_kind") or "auto"
    # Fail here, not halfway through a run that has already claimed tickets.
    analysis.get_analyst(kind)
    ctx["analyst_kind"] = kind
    ctx["sheet"] = requester.load_sheet(ctx.get("sheet_path"))
    ctx["rules"] = analysis.load_rules()


@agent("poll_intake", READ)
def poll_intake(ctx):
    """Read the open tickets on the intake board."""
    from agents.jira_leader.sources import JiraTicketSource

    source = ctx.get("source") or JiraTicketSource()
    issues = source.open_tickets()
    if ctx.get("limit"):
        issues = issues[:ctx["limit"]]
    ctx["issues"] = issues


@agent("reclaim_stale_claims", LOCAL)
def reclaim_stale_claims(ctx):
    """Release claims a dead triage left behind (CLAIMED > 30 min -> NEW)."""
    with ledger_mod.Ledger() as led:
        reclaimed = led.reclaim_stale_claims()
    if reclaimed:
        LOG.warn("queue.reclaimed_stale", tickets=reclaimed)
    ctx["reclaimed"] = reclaimed


@agent("claim_tickets", LOCAL)
def claim_tickets(ctx):
    """Apply the re-processing gate (invariant 4) and claim what passes."""
    claimed, skipped = [], []
    with ledger_mod.Ledger() as led:
        for issue in ctx["issues"]:
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
    ctx["claimed"], ctx["skipped"] = claimed, skipped
    LOG.info("queue.claimed", claimed=len(claimed), skipped=len(skipped),
             workers=min(ctx.get("max_workers") or MAX_WORKERS, max(len(claimed), 1)))


def _seed(ctx, issue: dict) -> dict:
    return {"issue": issue, "analyst_kind": ctx["analyst_kind"],
            "sheet": ctx["sheet"], "rules": ctx["rules"]}


@agent("summarise_intake", LOCAL)
def summarise_intake(ctx):
    """Collect each triage outcome into the sweep's summary."""
    outcomes = []
    for r in ctx.get("triaged") or []:
        o = r.get("outcome") or {"ticket": r["issue"]["key"], "state": "ERROR",
                                 "proposal_id": None,
                                 "reason": r.get("_error") or "no outcome"}
        outcomes.append(o)
    summary = {"claimed": len(ctx["claimed"]), "skipped": ctx["skipped"],
               "outcomes": sorted(outcomes, key=lambda o: o["ticket"])}
    LOG.info("queue.done", **{k: len(v) if isinstance(v, list) else v
                              for k, v in summary.items()})
    ctx["report"] = summary


INTAKE = Flow(
    "intake",
    [choose_analyst, poll_intake, reclaim_stale_claims, claim_tickets,
     ForEach("triage_each", "claimed", TRIAGE, _seed, collect="triaged",
             max_workers=MAX_WORKERS, workers_from="max_workers"),
     summarise_intake],
    triggers=[Every(300)],
    doc="Sweep PESD1, claim what the re-processing gate lets through, triage each.",
    timeout_seconds=1800,
)

FLOWS = (INTAKE,)
