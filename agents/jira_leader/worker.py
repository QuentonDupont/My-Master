"""Per-ticket triage: the CLI entry point and the helpers its agents share.

The steps — sanity gate, never-touch gate, research, requester, duplicate gate,
classify, risk, clone plan, draft — are single-purpose agents chained in
flows/triage.py. `process()` runs that flow for one ticket.

Triage writes to the ledger (its own state) and to the proposal store. It
never writes to Jira — there is no Jira write client in this module at all.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass

from agents.historian.retrieval import Historian
from agents.jira_leader import analysis as analysis_mod
from agents.jira_leader.sources import FileTicketSource, comment_count
from core import config, ledger as ledger_mod, log, proposals, requester as requester_mod

LOG = log.get("worker")


@dataclass
class Outcome:
    ticket: str
    state: str
    proposal_id: str | None
    reason: str

    def to_dict(self) -> dict:
        return {"ticket": self.ticket, "state": self.state,
                "proposal_id": self.proposal_id, "reason": self.reason}


URL_ONLY_RE = re.compile(r"^\s*(https?://\S+\s*)+$")


def unreadable_detail(issue: dict) -> str | None:
    """Is the actual request inside something we cannot read?

    Most PESD1 tickets carry the request in the summary; some carry it only in a
    Drive link or an attachment. The human has to be told when that is the case,
    rather than shown a confident proposal written from a title.
    """
    fields = issue.get("fields", {}) or {}
    description = (fields.get("description") or "").strip()
    attachments = fields.get("attachment") or []
    if description and URL_ONLY_RE.match(description):
        return "description is only a link — the detail is not readable"
    if attachments and not description:
        return f"{len(attachments)} attachment(s) and no description"
    return None


def _new_proposal(issue: dict, **kw) -> proposals.Proposal:
    return proposals.Proposal(
        proposal_id=proposals.next_id(),
        ticket=issue["key"],
        ticket_url=config.ticket_url(issue["key"]),
        **kw,
    )


def fixed_route(issue: dict) -> dict | None:
    """The configured route this ticket belongs to, if any.

    A ticket family the board owner has already decided how to handle
    (config/boards.yml `fixed_routes`) — e.g. every Crashlytics fatal-crash
    alert goes to Rahul Ravindra, verbatim, every time. A match skips
    duplicate search, the analyst and developer routing entirely; it still
    produces a proposal the human approves like any other.
    """
    summary = (issue.get("fields", {}) or {}).get("summary") or ""
    for route in config.boards().get("fixed_routes") or []:
        prefix = route.get("summary_prefix")
        if prefix and summary.strip().lower().startswith(prefix.lower()):
            return route
    return None


def routed_proposal(issue: dict, restated: str, route: dict) -> proposals.Proposal:
    """The proposal a fixed route produces: the clone copies the PESD1 ticket
    verbatim, labelled and assigned exactly as the route says."""
    fields = issue.get("fields", {}) or {}
    priority, _why, _flags = analysis_mod.clone_priority(issue)
    return _new_proposal(
        issue, requirement_restated=restated,
        requester=proposals.Requester(),
        classification=proposals.NEEDS_CODE, confidence=1.0,
        proposed_comment=route["comment"],
        clone=proposals.Clone(
            target_project=config.dev_project(),
            summary=fields.get("summary") or "",
            # Unedited below the rule, so validation can tell it from our line.
            description=(f"Copied unchanged from {issue['key']}.\n\n----\n\n"
                         f"{fields.get('description') or ''}"),
            assignee=route["assignee"],
            assignee_reason=f"fixed route '{route['name']}' (config/boards.yml)",
            assignee_alternates=[route["assignee"]],
            labels=list(route["labels"]),
            priority=priority,
            link_type=config.boards()["development"]["link_type"]),
        pesd1_transition=config.boards()["intake"]["dev_transition"],
        flags=[f"route:{route['name']}", "requester_unknown"])


def _finish(led: ledger_mod.Ledger, proposal: proposals.Proposal, state: str,
            reason: str) -> Outcome:
    problems = proposals.validate(proposal.to_dict())
    if problems:
        LOG.error("worker.invalid_proposal", ticket=proposal.ticket, problems=problems)
        led.record_error(proposal.ticket, "invalid proposal: " + "; ".join(problems))
        proposal.flags = list(proposal.flags) + ["invalid_proposal"]
    proposals.save(proposal)
    led.transition(proposal.ticket, state, proposal_id=proposal.proposal_id)
    LOG.info("worker.done", ticket=proposal.ticket, state=state,
             proposal=proposal.proposal_id, classification=proposal.classification)
    return Outcome(proposal.ticket, state, proposal.proposal_id, reason)


def process(issue: dict, *, ledger: ledger_mod.Ledger | None = None,
            historian: Historian | None = None, analyst=None,
            sheet: list[dict] | None = None, rules: str | None = None) -> Outcome:
    """Run the triage flow for one ticket. Caller has already claimed it.

    The steps themselves are single-purpose agents in flows/triage.py; this is
    the entry point the CLI, the queue and the tests call.
    """
    from flows.triage import TRIAGE

    result = TRIAGE.run(issue=issue, led=ledger, historian=historian,
                        analyst=analyst, sheet=sheet, rules=rules)
    o = result.data.get("outcome") or {
        "ticket": issue["key"], "state": "ERROR", "proposal_id": None,
        "reason": result.error or "triage produced no outcome"}
    return Outcome(o["ticket"], o["state"], o["proposal_id"], o["reason"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.worker",
                                 description="run the worker loop on one ticket")
    ap.add_argument("ticket")
    ap.add_argument("--file", default="tests/fixtures/inbox.jsonl")
    ap.add_argument("--sheet", default="tests/fixtures/intake_sheet.csv")
    ap.add_argument("--analyst", default="heuristic",
                    choices=["auto", "heuristic", "claude"])
    args = ap.parse_args(argv)

    issue = FileTicketSource(args.file).get(args.ticket)
    with ledger_mod.Ledger() as led:
        led.claim(issue["key"], ledger_mod.content_hash(
            issue["fields"].get("summary") or "",
            issue["fields"].get("description") or "", comment_count(issue)))
        outcome = process(issue, ledger=led,
                          analyst=analysis_mod.get_analyst(args.analyst),
                          sheet=requester_mod.load_sheet(args.sheet))
    print(json.dumps(outcome.to_dict(), indent=2))
    if outcome.proposal_id:
        print(proposals.load(outcome.proposal_id).to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
