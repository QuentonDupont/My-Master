"""Triage — one ticket in, one proposal out. A sub-flow of intake.

CLAUDE.md's worker loop, one agent per step:

    read_ticket -> sanity_gate -> never_touch_gate -> research ->
    resolve_requester -> duplicate_gate -> classify -> assess_risk ->
    plan_clone -> draft_proposal

The gates end the flow early with the ticket's outcome already recorded
(ESCALATED or DUPLICATE). Nothing here writes to Jira: the effects are read
(Jira payload, corpus, sheet) and local (ledger state, the proposal store).

If any agent fails, `release_ticket` records the error and moves the ticket
back to NEW, so the next sweep picks it up again — a crash never strands a
claim.

Context in:  issue, and optionally led, historian, analyst, sheet, rules.
Context out: outcome — {ticket, state, proposal_id, reason}.
"""
from __future__ import annotations

from core import config, ledger as ledger_mod, log, proposals
from core.flow import LOCAL, READ, Flow, agent

LOG = log.get("worker")


def _w():
    # Imported lazily: worker.process is itself a thin wrapper over this flow.
    from agents.jira_leader import worker
    return worker


def _decide(ctx, proposal: proposals.Proposal, state: str, reason: str) -> None:
    ctx["outcome"] = _w()._finish(ctx["led"], proposal, state, reason).to_dict()
    ctx.stop(reason)


@agent("read_ticket", READ)
def read_ticket(ctx):
    """Unpack the ticket and open the ledger, corpus and analyst it needs."""
    from agents.historian.retrieval import Historian
    from agents.jira_leader import analysis

    issue = ctx["issue"]
    fields = issue.get("fields", {}) or {}
    ctx["key"] = issue["key"]
    ctx["summary"] = fields.get("summary") or ""
    ctx["description"] = fields.get("description") or ""
    ctx["fields"] = fields
    if ctx.get("rules") is None:
        ctx["rules"] = analysis.load_rules()
    if ctx.get("analyst") is None:
        ctx["analyst"] = analysis.get_analyst(ctx.get("analyst_kind") or "auto")
    if ctx.get("led") is None:
        ctx["led"], ctx["_own_led"] = ledger_mod.Ledger(), True
    if ctx.get("historian") is None:
        ctx["historian"], ctx["_own_hist"] = Historian(), True


@agent("sanity_gate", LOCAL)
def sanity_gate(ctx):
    """Can the requirement be stated in one sentence? If not, escalate."""
    from agents.jira_leader.gates import restate

    restated, why = restate(ctx["issue"])
    if restated:
        ctx["restated"] = restated
        return
    _decide(ctx, _w()._new_proposal(
        ctx["issue"],
        requirement_restated=f"Unclear — {why}. A human needs to read this one.",
        requester=proposals.Requester(),
        classification=proposals.ESCALATE, confidence=0.0,
        flags=["sanity_gate", "requester_unknown", f"reason:{why}"]),
        ledger_mod.ESCALATED, f"sanity gate: {why}")


@agent("never_touch_gate", LOCAL)
def never_touch_gate(ctx):
    """Never-touch subject? Escalate with no comment and no clone (invariant 3)."""
    from agents.jira_leader.gates import never_touch

    hits = never_touch(ctx["issue"])
    if not hits:
        return
    categories = sorted({h["category"] for h in hits})
    _decide(ctx, _w()._new_proposal(
        ctx["issue"], requirement_restated=ctx["restated"],
        requester=proposals.Requester(),
        classification=proposals.ESCALATE, confidence=0.0,
        flags=["never_touch"] + [f"never_touch:{c}" for c in categories]
              + ["requester_unknown"]),
        ledger_mod.ESCALATED, f"never-touch: {', '.join(categories)}")


@agent("research", READ)
def research(ctx):
    """Historian: duplicates, related open work, similar resolved tickets, SOPs."""
    ctx["research"] = ctx["historian"].research(ctx["key"], ctx["summary"],
                                                ctx["description"])


@agent("resolve_requester", READ)
def resolve_requester(ctx):
    """Who asked — Jira field, sheet join, or a three-way match. Never a guess."""
    from core import requester as requester_mod

    ctx["requester"] = requester_mod.resolve(ctx["issue"], ctx.get("sheet"))


@agent("duplicate_gate", LOCAL)
def duplicate_gate(ctx):
    """A strong match on an open ticket is linked, never cloned."""
    found = ctx["research"].duplicates
    if not found:
        return
    top, requester = found[0], ctx["requester"]
    _decide(ctx, _w()._new_proposal(
        ctx["issue"], requirement_restated=ctx["restated"],
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
        flags=list(requester.flags) + ["duplicate_link_only"]),
        ledger_mod.DUPLICATE, f"duplicate of {top['ref']}")


@agent("classify", READ)
def classify(ctx):
    """ANSWERABLE or NEEDS_CODE, with a drafted comment, from the analyst."""
    ctx["analysis"] = ctx["analyst"].analyse(ctx["issue"], ctx["research"],
                                             ctx["restated"], ctx["rules"])


@agent("assess_risk", LOCAL)
def assess_risk(ctx):
    """Flag what should lower confidence: open dev work, unreadable detail."""
    analysis, research, key = ctx["analysis"], ctx["research"], ctx["key"]
    flags = list(dict.fromkeys(list(analysis.flags) + list(ctx["requester"].flags)))
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
                     clones=[c["key"] for d in with_clones for c in d["open_clones"]])
    if _w().unreadable_detail(ctx["issue"]):
        flags.append("detail_in_attachment")
        # We drafted from the summary alone; say so in the confidence.
        analysis.confidence = round(min(analysis.confidence, 0.45), 2)
        if "low_confidence" not in flags:
            flags.append("low_confidence")
    ctx["flags"] = flags


@agent("plan_clone", READ)
def plan_clone(ctx):
    """For NEEDS_CODE: the PRDT clone — assignee shortlist, priority, brief."""
    from agents.jira_leader import analysis as analysis_mod, description as description_mod

    analysis = ctx["analysis"]
    ctx["clone"], ctx["transition"] = None, None
    if analysis.classification != proposals.NEEDS_CODE:
        return
    issue, research, flags = ctx["issue"], ctx["research"], ctx["flags"]
    candidates = ctx["historian"].assignee_candidates(
        f"{ctx['summary']} {ctx['description']}", labels=analysis.labels,
        components=research.components)
    first = candidates[0] if candidates else None
    priority, priority_why, priority_flags = analysis_mod.clone_priority(issue)
    flags.extend(priority_flags)
    LOG.info("worker.priority", ticket=ctx["key"], priority=priority, why=priority_why)
    ctx["clone"] = proposals.Clone(
        target_project=config.dev_project(),
        summary=analysis.clone_summary or ctx["summary"],
        description=description_mod.build(issue, research, analysis, ctx["requester"]),
        assignee=first["assignee"] if first else None,
        assignee_reason=first["reason"] if first else "",
        assignee_alternates=[c["assignee"] for c in candidates[1:]] or
                            ([first["assignee"]] if first else ["unassigned"]),
        labels=analysis.labels,
        priority=priority,
        link_type=config.boards()["development"]["link_type"])
    ctx["transition"] = config.boards()["intake"]["dev_transition"]
    if not candidates:
        flags.append("no_assignee_candidate")


@agent("draft_proposal", LOCAL)
def draft_proposal(ctx):
    """Assemble, validate and store the proposal; ledger -> PROPOSED."""
    analysis, requester, fields = ctx["analysis"], ctx["requester"], ctx["fields"]
    proposal = _w()._new_proposal(
        ctx["issue"], requirement_restated=analysis.requirement_restated or ctx["restated"],
        requester=proposals.Requester(**requester.to_dict()),
        classification=analysis.classification,
        confidence=analysis.confidence,
        proposed_comment=analysis.proposed_comment,
        evidence=[proposals.Evidence(**e) for e in ctx["research"].evidence()],
        clone=ctx["clone"], pesd1_transition=ctx["transition"],
        flags=ctx["flags"])
    field_id = config.boards()["intake"].get("requester_email_field")
    if (field_id and requester.source == "sheet" and requester.confidence == "high"
            and requester.email and not fields.get(field_id)):
        proposal.requester_email_update = proposals.RequesterEmailUpdate(
            field_id, fields.get(field_id), requester.email)
    ctx["outcome"] = _w()._finish(ctx["led"], proposal, ledger_mod.PROPOSED,
                                  f"{analysis.classification} via {analysis.analyst}"
                                  ).to_dict()


def release_ticket(ctx, exc: BaseException) -> None:
    """A failed triage must not take the queue down or strand the claim."""
    key = ctx.get("key") or ctx["issue"].get("key")
    LOG.error("worker.crashed", ticket=key, error=str(exc))
    led = ctx.get("led")
    if led is not None:
        led.record_error(key, f"worker: {exc}")
        try:
            led.transition(key, ledger_mod.NEW)
        except ledger_mod.LedgerError:
            pass
    ctx["outcome"] = {"ticket": key, "state": "ERROR", "proposal_id": None,
                      "reason": str(exc)}


def close_resources(ctx) -> None:
    if ctx.get("_own_hist"):
        ctx["historian"].close()
    if ctx.get("_own_led"):
        ctx["led"].close()


TRIAGE = Flow(
    "triage",
    [read_ticket, sanity_gate, never_touch_gate, research, resolve_requester,
     duplicate_gate, classify, assess_risk, plan_clone, draft_proposal],
    doc="One claimed ticket -> one proposal (or an escalation / duplicate). No Jira writes.",
    on_failure=release_ticket,
    cleanup=close_resources,
)

# A sub-flow: run once per claimed ticket by intake, never scheduled on its own.
FLOWS = ()
