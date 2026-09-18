"""The ephemeral per-ticket worker. One ticket in, one Proposal out.

Steps, in the order CLAUDE.md fixes them:
  1 read -> 2 sanity gate -> 3 never-touch gate -> 4 duplicate check ->
  5 retrieve -> 6 requester -> 7 classify -> 8 draft proposal.

The worker writes to the ledger (its own state) and to the proposal store. It
never writes to Jira — there is no Jira write client in this module at all.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass

from agents.historian.retrieval import Historian
from agents.jira_leader import analysis as analysis_mod
from agents.jira_leader import description as description_mod
from agents.jira_leader.gates import never_touch, restate
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
    """Run the worker loop for one ticket. Caller has already claimed it."""
    key = issue["key"]
    fields = issue.get("fields", {}) or {}
    summary = fields.get("summary") or ""
    description = fields.get("description") or ""
    rules = analysis_mod.load_rules() if rules is None else rules
    analyst = analyst or analysis_mod.get_analyst()

    own_ledger = ledger is None
    own_hist = historian is None
    led = ledger or ledger_mod.Ledger()
    hist = historian or Historian()
    try:
        # 2. sanity gate
        restated, why = restate(issue)
        if not restated:
            proposal = _new_proposal(
                issue,
                requirement_restated=f"Unclear — {why}. A human needs to read this one.",
                requester=proposals.Requester(),
                classification=proposals.ESCALATE, confidence=0.0,
                flags=["sanity_gate", "requester_unknown", f"reason:{why}"])
            return _finish(led, proposal, ledger_mod.ESCALATED, f"sanity gate: {why}")

        # 3. never-touch gate — no comment, no clone, whatever the confidence
        hits = never_touch(issue)
        if hits:
            categories = sorted({h["category"] for h in hits})
            proposal = _new_proposal(
                issue, requirement_restated=restated,
                requester=proposals.Requester(),
                classification=proposals.ESCALATE, confidence=0.0,
                flags=["never_touch"] + [f"never_touch:{c}" for c in categories]
                      + ["requester_unknown"])
            return _finish(led, proposal, ledger_mod.ESCALATED,
                           f"never-touch: {', '.join(categories)}")

        # 4. duplicate check — a duplicate is never cloned
        research = hist.research(key, summary, description)
        requester = requester_mod.resolve(issue, sheet)
        if research.duplicates:
            top = research.duplicates[0]
            proposal = _new_proposal(
                issue, requirement_restated=restated,
                requester=proposals.Requester(**requester.to_dict()),
                classification=proposals.DUPLICATE,
                confidence=round(float(top.get("similarity", 0)), 2),
                proposed_comment=(
                    f"This looks like a duplicate of {top['ref']} ({top['title']}), "
                    f"which is already open. Linking the two and continuing there so "
                    f"the history stays in one place."),
                evidence=[proposals.Evidence(
                    type="jira", ref=top["ref"],
                    why=f"open ticket, {top['similarity']:.0%} match on the symptom")],
                flags=list(requester.flags) + ["duplicate_link_only"])
            return _finish(led, proposal, ledger_mod.DUPLICATE,
                           f"duplicate of {top['ref']}")

        # 5-7. retrieve (done), requester (done), classify
        analysis = analyst.analyse(issue, research, restated, rules)

        evidence = [proposals.Evidence(**e) for e in research.evidence()]
        flags = list(dict.fromkeys(list(analysis.flags) + list(requester.flags)))
        if research.related:
            flags.append("possible_duplicate")
            with_clones = [d for d in research.related if d.get("open_clones")]
            if with_clones:
                flags.append("open_dev_work_exists")
                # Someone is already building this. Do not let a confident-looking
                # clone proposal hide that.
                analysis.confidence = round(min(analysis.confidence, 0.4), 2)
                LOG.warn("worker.open_dev_work", ticket=key,
                         related=[d["ref"] for d in with_clones],
                         clones=[c["key"] for d in with_clones
                                 for c in d["open_clones"]])

        unreadable = unreadable_detail(issue)
        if unreadable:
            flags.append("detail_in_attachment")
            # We drafted from the summary alone; say so in the confidence.
            analysis.confidence = round(min(analysis.confidence, 0.45), 2)
            if "low_confidence" not in flags:
                flags.append("low_confidence")

        clone = None
        transition = None
        if analysis.classification == proposals.NEEDS_CODE:
            candidates = hist.assignee_candidates(
                f"{summary} {description}", labels=analysis.labels,
                components=research.components)
            first = candidates[0] if candidates else None
            clone = proposals.Clone(
                target_project=config.dev_project(),
                summary=analysis.clone_summary or summary,
                description=description_mod.build(issue, research, analysis,
                                                 requester),
                assignee=first["assignee"] if first else None,
                assignee_reason=first["reason"] if first else "",
                assignee_alternates=[c["assignee"] for c in candidates[1:]] or
                                    ([first["assignee"]] if first else ["unassigned"]),
                labels=analysis.labels,
                priority=config.boards()["development"]["default_priority"],
                link_type=config.boards()["development"]["link_type"])
            transition = config.boards()["intake"]["dev_transition"]
            if not candidates:
                flags.append("no_assignee_candidate")

        # 8. draft the proposal. Nothing is written to Jira.
        proposal = _new_proposal(
            issue, requirement_restated=analysis.requirement_restated or restated,
            requester=proposals.Requester(**requester.to_dict()),
            classification=analysis.classification,
            confidence=analysis.confidence,
            proposed_comment=analysis.proposed_comment,
            evidence=evidence, clone=clone, pesd1_transition=transition,
            flags=flags)
        return _finish(led, proposal, ledger_mod.PROPOSED,
                       f"{analysis.classification} via {analysis.analyst}")
    except Exception as exc:  # a worker failure must not take the queue down
        LOG.error("worker.crashed", ticket=key, error=str(exc))
        led.record_error(key, f"worker: {exc}")
        try:
            led.transition(key, ledger_mod.NEW)
        except ledger_mod.LedgerError:
            pass
        return Outcome(key, "ERROR", None, str(exc))
    finally:
        if own_hist:
            hist.close()
        if own_ledger:
            led.close()


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
