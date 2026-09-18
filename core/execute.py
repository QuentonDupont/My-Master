"""The ONLY Jira write path.

    execute_proposal(proposal_id, execute=False)  # dry run unless --execute
    undo(ticket_key, execute=False)

Order (CLAUDE.md): comment -> clone -> link -> assign -> transition.
Every step is journalled. If a step fails, that step alone is rolled back, the
error lands in ledger.last_error, and the sequence stops — the ticket does NOT
reach EXECUTED, so the human can look and re-run.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from core import config, ledger as ledger_mod, log, proposals, recommendations
from core.jira_client import JiraError, JiraReadClient, JiraWriteClient

LOG = log.get("execute")

EXECUTABLE_STATES = (ledger_mod.APPROVED, ledger_mod.CORRECTED)

#: Jira Cloud accountIds: the legacy 24-char alphanumeric id (e.g.
#: 5b10a2844c20165700ede21g) or the newer "<digits>:<uuid>" form.
ACCOUNT_ID_RE = re.compile(r"^(?:[0-9a-z]{24}|\d{6,}:[0-9a-f-]{20,})$", re.I)


class ExecutionRefused(RuntimeError):
    """Refusal by an invariant. Never caught and retried automatically."""


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ExecutionResult:
    proposal_id: str
    ticket: str
    dry_run: bool
    steps: list[StepResult] = field(default_factory=list)
    comment_id: str | None = None
    clone_key: str | None = None
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id, "ticket": self.ticket,
            "dry_run": self.dry_run, "ok": self.ok,
            "comment_id": self.comment_id, "clone_key": self.clone_key,
            "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail, "error": s.error}
                      for s in self.steps],
        }


def _resolve_account_id(reader: JiraReadClient, assignee: str | None) -> str | None:
    """Accept an accountId, an email or a display name. Never invent a user."""
    if not assignee:
        return None
    if ACCOUNT_ID_RE.match(assignee):
        return assignee  # already an accountId
    matches = reader.find_users(assignee)
    if len(matches) == 1:
        return matches[0]["accountId"]
    if not matches:
        raise ExecutionRefused(f"no Jira user matches assignee {assignee!r}")
    exact = [m for m in matches
             if assignee.lower() in (m.get("emailAddress") or "").lower()
             or assignee.lower() == (m.get("displayName") or "").lower()]
    if len(exact) == 1:
        return exact[0]["accountId"]
    raise ExecutionRefused(
        f"assignee {assignee!r} is ambiguous ({len(matches)} matches) — fix the proposal"
    )


def screen_fields(fields: dict, issue_type: str,
                  reader: JiraReadClient | None = None) -> dict:
    """Drop fields this issue type's create screen does not carry.

    Required fields are per issue type: PRDT demands Department on a Story and
    rejects it on an Epic. Sending everything to everything fails the create.
    """
    if not fields:
        return {}
    try:
        allowed = (reader or JiraReadClient()).creatable_fields(
            config.dev_project(), issue_type)
    except JiraError as exc:  # pragma: no cover - network path
        LOG.warn("execute.screen_lookup_failed", error=str(exc)[:200])
        return fields
    if not allowed:
        return fields
    kept = {k: v for k, v in fields.items() if k in allowed}
    dropped = sorted(set(fields) - set(kept))
    if dropped:
        LOG.info("execute.fields_not_on_screen", issue_type=issue_type,
                 dropped=dropped)
    return kept


def clone_fields(clone) -> dict:
    """Extra fields PRDT demands on create, including the component's epic."""
    dev = config.boards()["development"]
    fields = dict(dev.get("required_fields") or {})
    epic_field = dev.get("epic_link_field")
    if not epic_field:
        return fields
    mapping = dev.get("epic_by_component") or {}
    component = (clone.labels or [None])[0]
    epic = mapping.get(component) or dev.get("default_epic") or ""
    if not epic:
        raise ExecutionRefused(
            f"no epic configured for component {component!r}: set "
            f"development.epic_by_component.{component} or development.default_epic "
            f"in config/boards.yml — {config.dev_project()} rejects a Story without one"
        )
    fields[epic_field] = epic
    return fields


def _drop_link(writer: JiraWriteClient, clone_key: str, ticket: str) -> None:
    link_id = writer.find_link_id(clone_key, ticket)
    if link_id:
        writer.delete_link(link_id)


def _preflight(proposal: proposals.Proposal, led: ledger_mod.Ledger) -> dict:
    """Every refusal an invariant demands, before a single byte is written."""
    row = led.get(proposal.ticket)
    if row is None:
        raise ExecutionRefused(f"{proposal.ticket} is not in the ledger")
    if row["state"] == ledger_mod.EXECUTED:
        raise ExecutionRefused(f"{proposal.ticket} is already EXECUTED (invariant 5)")
    if row["state"] not in EXECUTABLE_STATES:
        raise ExecutionRefused(
            f"{proposal.ticket} is {row['state']}; only "
            f"{'/'.join(EXECUTABLE_STATES)} may be executed (invariant 1)"
        )
    if row["proposal_id"] != proposal.proposal_id:
        raise ExecutionRefused(
            f"ledger holds proposal {row['proposal_id']!r}, not {proposal.proposal_id!r}"
        )
    problems = proposals.validate(proposal.to_dict())
    if problems:
        raise ExecutionRefused("proposal does not validate: " + "; ".join(problems))
    if proposal.classification == proposals.ESCALATE:
        raise ExecutionRefused("ESCALATE proposals are for the human; nothing to write")
    return row


def execute_proposal(proposal_id: str, *, execute: bool = False,
                     ledger: ledger_mod.Ledger | None = None,
                     writer: JiraWriteClient | None = None,
                     reader: JiraReadClient | None = None) -> ExecutionResult:
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        proposal = proposals.load(proposal_id)
        row = _preflight(proposal, led)
        reader = reader or JiraReadClient()
        writer = writer or JiraWriteClient(execute=execute)
        if getattr(writer, "execute", False) != bool(execute):
            raise ExecutionRefused("writer execute flag disagrees with --execute")

        result = ExecutionResult(proposal_id=proposal_id, ticket=proposal.ticket,
                                 dry_run=not execute)
        ticket = proposal.ticket
        clone = proposal.clone
        # A previous attempt may have completed some steps before failing. Those
        # are recorded on the ledger row; never repeat them.
        done_comment = row["comment_id"] if execute else None
        done_clone = row["clone_key"] if execute else None
        if done_comment or done_clone:
            LOG.info("execute.resuming", ticket=ticket, comment_id=done_comment,
                     clone_key=done_clone)
        prior_status: str | None = None
        if execute:
            try:
                prior_status = writer.current_status(ticket)
                led.journal(ticket, proposal_id, "record_prior_status", True,
                            {"status": prior_status})
            except Exception as exc:  # pragma: no cover - network path
                LOG.warn("execute.prior_status_unavailable", ticket=ticket, error=str(exc))

        def run(name: str, action: Callable[[], Any], detail: dict,
                compensate: Callable[[], Any] | None = None) -> Any:
            """Run one step. On failure, roll back THIS item only and stop."""
            try:
                out = action()
            except Exception as exc:
                rolled_back = None
                if compensate is not None and execute:
                    try:
                        compensate()
                        rolled_back = True
                    except Exception as undo_exc:
                        rolled_back = False
                        LOG.error("execute.compensate_failed", ticket=ticket, step=name,
                                  error=str(undo_exc))
                led.journal(ticket, proposal_id, name, False,
                            {"error": str(exc), "rolled_back": rolled_back, **detail})
                led.record_error(ticket, f"{name}: {exc}")
                result.steps.append(StepResult(name, False, {**detail,
                                                             "rolled_back": rolled_back},
                                               str(exc)))
                result.ok = False
                raise _StepFailed(name) from exc
            led.journal(ticket, proposal_id, name, True, detail)
            result.steps.append(StepResult(name, True, detail))
            return out

        try:
            # 1. comment on PESD1
            if done_comment:
                result.comment_id = done_comment
                result.steps.append(StepResult("comment", True,
                                               {"skipped": "already posted",
                                                "comment_id": done_comment}))
            else:
                commented = run(
                    "comment",
                    lambda: writer.add_comment(ticket, proposal.proposed_comment),
                    {"ticket": ticket, "chars": len(proposal.proposed_comment)},
                )
                result.comment_id = (commented.get("id")
                                     if isinstance(commented, dict) else None)
                if execute and result.comment_id:
                    led.note_progress(ticket, comment_id=result.comment_id)
                    led.journal(ticket, proposal_id, "comment.id", True,
                                {"comment_id": result.comment_id})

            if clone:
                # 2. create the PRDT clone
                if done_clone:
                    created = {"key": done_clone}
                    result.steps.append(StepResult("clone", True,
                                                   {"skipped": "already created",
                                                    "key": done_clone}))
                else:
                    created = run(
                        "clone",
                        lambda: writer.create_issue(
                            clone.target_project, clone.summary, clone.description,
                            issue_type=config.dev_issue_type(),
                            labels=clone.labels, priority=clone.priority,
                            extra_fields=screen_fields(
                                clone_fields(clone), config.dev_issue_type(),
                                reader) if execute else clone_fields(clone)),
                        {"project": clone.target_project,
                         "issue_type": config.dev_issue_type(),
                         "summary": clone.summary[:120]},
                    )
                # In a dry run there is no real key; use a placeholder so the
                # remaining steps still run and get logged.
                result.clone_key = (created.get("key") if isinstance(created, dict) else None) \
                    or (f"{clone.target_project}-0" if not execute else None)
                if not result.clone_key:
                    raise _StepFailed("clone")
                clone_key = result.clone_key
                if execute and not done_clone:
                    led.note_progress(ticket, clone_key=clone_key)

                # 3. link the two
                run("link",
                    lambda: writer.link_issues(clone_key, ticket, clone.link_type),
                    {"inward": clone_key, "outward": ticket, "type": clone.link_type},
                    compensate=lambda: _drop_link(writer, clone_key, ticket))

                # 4. assign
                if clone.assignee:
                    account_id = (_resolve_account_id(reader, clone.assignee)
                                  if execute else clone.assignee)
                    run("assign",
                        lambda: writer.assign(clone_key, account_id),
                        {"key": clone_key, "assignee": clone.assignee},
                        compensate=lambda: writer.unassign(clone_key))

            # 5. transition PESD1
            if proposal.pesd1_transition:
                run("transition",
                    lambda: writer.transition(ticket, proposal.pesd1_transition),
                    {"ticket": ticket, "to": proposal.pesd1_transition},
                    compensate=(lambda: writer.transition(ticket, prior_status))
                    if prior_status else None)
        except _StepFailed as failure:
            LOG.error("execute.failed", ticket=ticket, proposal=proposal_id,
                      step=failure.step, comment_id=result.comment_id,
                      clone_key=result.clone_key)
            # State stays APPROVED/CORRECTED: the human inspects, then re-runs or
            # undoes. last_error already carries the failing step.
            return result

        if execute:
            led.transition(ticket, ledger_mod.EXECUTED,
                           comment_id=result.comment_id,
                           clone_key=result.clone_key,
                           last_error=None)
            LOG.info("execute.done", ticket=ticket, proposal=proposal_id,
                     clone=result.clone_key)
        else:
            LOG.info("execute.dry_run", ticket=ticket, proposal=proposal_id,
                     steps=[s.name for s in result.steps])
        return result
    finally:
        if own_ledger:
            led.close()


class _StepFailed(RuntimeError):
    def __init__(self, step: str) -> None:
        self.step = step
        super().__init__(step)


def undo(ticket_key: str, *, execute: bool = False,
         ledger: ledger_mod.Ledger | None = None,
         writer: JiraWriteClient | None = None) -> ExecutionResult:
    """Reverse an execution: transition back, unassign, unlink, delete clone, delete comment."""
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        row = led.get(ticket_key)
        if row is None:
            raise ExecutionRefused(f"{ticket_key} is not in the ledger")
        if row["state"] != ledger_mod.EXECUTED:
            raise ExecutionRefused(f"{ticket_key} is {row['state']}, nothing to undo")
        writer = writer or JiraWriteClient(execute=execute)
        result = ExecutionResult(proposal_id=row["proposal_id"] or "", ticket=ticket_key,
                                 dry_run=not execute, comment_id=row["comment_id"],
                                 clone_key=row["clone_key"])

        prior = None
        for entry in led.journal_for(ticket_key):
            if entry["step"] == "record_prior_status" and entry["detail"]:
                prior = json.loads(entry["detail"]).get("status")

        def step(name: str, fn: Callable[[], Any], detail: dict) -> None:
            try:
                fn()
                led.journal(ticket_key, row["proposal_id"], f"undo.{name}", True, detail)
                result.steps.append(StepResult(f"undo.{name}", True, detail))
            except Exception as exc:
                led.journal(ticket_key, row["proposal_id"], f"undo.{name}", False,
                            {"error": str(exc), **detail})
                result.steps.append(StepResult(f"undo.{name}", False, detail, str(exc)))
                result.ok = False

        if prior:
            step("transition", lambda: writer.transition(ticket_key, prior), {"to": prior})
        if row["clone_key"]:
            step("unassign", lambda: writer.unassign(row["clone_key"]),
                 {"key": row["clone_key"]})
            link_id = writer.find_link_id(row["clone_key"], ticket_key) if execute else None
            if link_id:
                step("unlink", lambda: writer.delete_link(link_id), {"link_id": link_id})
            # The system does not delete tickets. The clone stays, unassigned and
            # unlinked, and closing it is recommended to the human.
            if execute:
                recommendations.recommend_close(
                    row["clone_key"],
                    f"created for {ticket_key}, whose execution was rolled back",
                    source_ticket=ticket_key)
            result.steps.append(StepResult("recommend_close", True,
                                           {"key": row["clone_key"],
                                            "action": recommendations.CLOSE}))
        if row["comment_id"]:
            step("delete_comment",
                 lambda: writer.delete_comment(ticket_key, row["comment_id"]),
                 {"comment_id": row["comment_id"]})

        if execute and result.ok:
            led.transition(ticket_key, ledger_mod.ROLLED_BACK,
                           clone_key=None, comment_id=None,
                           last_error="rolled back by undo()")
            LOG.info("undo.done", ticket=ticket_key)
        elif execute:
            led.record_error(ticket_key, "undo incomplete — see execution_log")
        return result
    finally:
        if own_ledger:
            led.close()


def create_epic(summary: str, *, description: str = "", execute: bool = False,
                writer: JiraWriteClient | None = None) -> dict:
    """Create the epic that support-escalation clones hang under.

    Invariant 1 keeps every TRIAGE write behind an approved proposal, and this is
    not one: it is a one-off setup action the board owner asked for by name. It
    lives here, in the single write module, so there is still exactly one file
    that can write to Jira — a worker cannot reach it, and it does nothing
    without --execute.
    """
    dev = config.boards()["development"]
    project = config.dev_project()
    writer = writer or JiraWriteClient(execute=execute)
    fields = dict(dev.get("required_fields") or {})
    epic_name_field = dev.get("epic_name_field")
    if epic_name_field:
        fields[epic_name_field] = summary
    result = writer.create_issue(
        project, summary,
        description or f"Umbrella for tickets cloned from {config.intake_project()} "
                       f"by the triage system. Each child links back to its "
                       f"{config.intake_project()} ticket.",
        issue_type="Epic",
        extra_fields=screen_fields(fields, "Epic") if execute else fields)
    LOG.info("execute.create_epic", project=project, summary=summary,
             dry_run=not execute, key=result.get("key"))
    return result


def redescribe_clone(ticket_key: str, *, execute: bool = False,
                     ledger: ledger_mod.Ledger | None = None,
                     writer: JiraWriteClient | None = None,
                     reader: JiraReadClient | None = None) -> ExecutionResult:
    """Rewrite an executed clone's description from the current template.

    The proposal was approved; this changes how the same facts are presented to
    the developer, not what was decided. The previous text is journalled first,
    so `--restore` can put it back.
    """
    from agents.historian.retrieval import Historian
    from agents.jira_leader import analysis as analysis_mod
    from agents.jira_leader import description as description_mod
    from agents.jira_leader.gates import restate
    from core import requester as requester_mod

    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        row = led.get(ticket_key)
        if row is None or not row["clone_key"]:
            raise ExecutionRefused(f"{ticket_key} has no clone to rewrite")
        proposal = proposals.load(row["proposal_id"])
        reader = reader or JiraReadClient()
        writer = writer or JiraWriteClient(execute=execute)
        issue = reader.issue(ticket_key)
        fields = issue.get("fields", {}) or {}

        with Historian() as hist:
            research = hist.research(ticket_key, fields.get("summary") or "",
                                     fields.get("description") or "")
            analysis = analysis_mod.get_analyst("heuristic").analyse(
                issue, research, restate(issue)[0] or proposal.requirement_restated,
                analysis_mod.load_rules())
            analysis.requirement_restated = proposal.requirement_restated
            text = description_mod.build(issue, research, analysis,
                                         requester_mod.resolve(issue, None))

        result = ExecutionResult(proposal_id=proposal.proposal_id, ticket=ticket_key,
                                 dry_run=not execute, clone_key=row["clone_key"])

        # Priority is carried from the PESD1 ticket; a clone created before that
        # rule existed is sitting on the default.
        from agents.jira_leader.analysis import clone_priority

        wanted_priority, priority_why, _ = clone_priority(issue)
        clone_fields_now = reader.issue(row["clone_key"],
                                        fields="description,priority").get("fields", {})
        current_priority = ((clone_fields_now.get("priority") or {}) or {}).get("name")
        if wanted_priority and current_priority != wanted_priority:
            if execute:
                led.journal(ticket_key, proposal.proposal_id, "repriority.previous",
                            True, {"clone": row["clone_key"],
                                   "priority": current_priority})
            try:
                writer.set_priority(row["clone_key"], wanted_priority)
                if proposal.clone:
                    proposal.clone.priority = wanted_priority
                    proposals.save(proposal)
                result.steps.append(StepResult(
                    "priority", True, {"key": row["clone_key"],
                                       "from": current_priority,
                                       "to": wanted_priority, "why": priority_why}))
            except Exception as exc:
                result.ok = False
                result.steps.append(StepResult("priority", False,
                                               {"key": row["clone_key"]}, str(exc)))

        previous = clone_fields_now.get("description") or ""
        if previous.strip() == text.strip():
            result.steps.append(StepResult("redescribe", True, {"skipped": "unchanged"}))
            return result
        if execute:
            led.journal(ticket_key, proposal.proposal_id, "redescribe.previous", True,
                        {"clone": row["clone_key"], "description": previous})
        try:
            writer.set_description(row["clone_key"], text)
            proposal.clone.description = text
            proposals.save(proposal)
            result.steps.append(StepResult("redescribe", True,
                                           {"key": row["clone_key"],
                                            "chars": len(text),
                                            "was_chars": len(previous)}))
            led.journal(ticket_key, proposal.proposal_id, "redescribe", True,
                        {"clone": row["clone_key"], "chars": len(text)})
        except Exception as exc:
            result.ok = False
            result.steps.append(StepResult("redescribe", False,
                                           {"key": row["clone_key"]}, str(exc)))
            led.record_error(ticket_key, f"redescribe: {exc}")
        return result
    finally:
        if own_ledger:
            led.close()


def restore_description(ticket_key: str, *, execute: bool = False,
                        ledger: ledger_mod.Ledger | None = None,
                        writer: JiraWriteClient | None = None) -> ExecutionResult:
    """Undo of redescribe_clone: put back the text journalled before the rewrite."""
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        row = led.get(ticket_key)
        if row is None or not row["clone_key"]:
            raise ExecutionRefused(f"{ticket_key} has no clone")
        previous = None
        for entry in led.journal_for(ticket_key):
            if entry["step"] == "redescribe.previous" and entry["detail"]:
                previous = json.loads(entry["detail"]).get("description")
        if previous is None:
            raise ExecutionRefused(f"no previous description journalled for {ticket_key}")
        writer = writer or JiraWriteClient(execute=execute)
        result = ExecutionResult(proposal_id=row["proposal_id"] or "", ticket=ticket_key,
                                 dry_run=not execute, clone_key=row["clone_key"])
        writer.restore_description(row["clone_key"], previous)
        result.steps.append(StepResult("restore_description", True,
                                       {"key": row["clone_key"],
                                        "chars": len(previous)}))
        return result
    finally:
        if own_ledger:
            led.close()


def close_recommended(ticket_key: str, *, execute: bool = False,
                      status: str | None = None,
                      ledger: ledger_mod.Ledger | None = None,
                      writer: JiraWriteClient | None = None,
                      reader: JiraReadClient | None = None) -> ExecutionResult:
    """Close a ticket the system recommended closing, once a human approves.

    Invariant 9 forbids DELETING a ticket and makes closing a human decision.
    This is that decision being carried out: it refuses unless an open
    recommendation exists for exactly this ticket, and it records the status it
    came from so `reopen()` can put it back. Nothing here deletes anything.
    """
    from core import recommendations

    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        entry = next((r for r in recommendations.open_items()
                      if r["ticket"] == ticket_key
                      and r["action"] == recommendations.CLOSE), None)
        if entry is None:
            raise ExecutionRefused(
                f"no open '{recommendations.CLOSE}' recommendation for {ticket_key} — "
                f"the system does not close a ticket on its own initiative")
        target = status or config.boards()["development"]["resolved_statuses"][-1]
        reader = reader or JiraReadClient()
        writer = writer or JiraWriteClient(execute=execute)
        before = reader.issue(ticket_key, fields="status")["fields"]["status"]["name"]

        result = ExecutionResult(proposal_id=entry.get("source_ticket") or "",
                                 ticket=ticket_key, dry_run=not execute)
        required = writer.transition_fields(ticket_key, target) if execute else {}
        fields = {}
        if "resolution" in required:
            fields["resolution"] = {
                "name": config.boards()["development"].get("close_resolution")
                        or "Won't Do"}
        try:
            writer.transition(ticket_key, target, fields=fields or None)
            if execute:
                led.journal(ticket_key, None, "close_recommended", True,
                            {"from": before, "to": target, "reason": entry["reason"],
                             "fields": fields})
                recommendations.resolve(entry["id"], "done")
            result.steps.append(StepResult("close", True,
                                           {"key": ticket_key, "from": before,
                                            "to": target}))
        except Exception as exc:
            result.ok = False
            result.steps.append(StepResult("close", False, {"key": ticket_key},
                                           str(exc)))
        LOG.info("execute.close_recommended", ticket=ticket_key, to=target,
                 ok=result.ok, dry_run=not execute)
        return result
    finally:
        if own_ledger:
            led.close()


def reopen(ticket_key: str, *, execute: bool = False,
           ledger: ledger_mod.Ledger | None = None,
           writer: JiraWriteClient | None = None) -> ExecutionResult:
    """Undo of close_recommended: back to the status it was closed from."""
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        previous = None
        for entry in led.journal_for(ticket_key):
            if entry["step"] == "close_recommended" and entry["detail"]:
                previous = json.loads(entry["detail"]).get("from")
        if not previous:
            raise ExecutionRefused(f"no recorded close for {ticket_key}")
        writer = writer or JiraWriteClient(execute=execute)
        result = ExecutionResult(proposal_id="", ticket=ticket_key,
                                 dry_run=not execute)
        writer.transition(ticket_key, previous)
        result.steps.append(StepResult("reopen", True, {"key": ticket_key,
                                                        "to": previous}))
        return result
    finally:
        if own_ledger:
            led.close()


def cleanup_partial(ticket_key: str, *, execute: bool = False,
                    ledger: ledger_mod.Ledger | None = None,
                    writer: JiraWriteClient | None = None,
                    reader: JiraReadClient | None = None) -> ExecutionResult:
    """Undo a half-finished execution so the proposal can be run again cleanly.

    A step can succeed and the next one fail; worse, a failed attempt that
    predates progress recording can leave a comment nothing remembers. This
    finds every comment on the ticket whose body is this proposal's text,
    deletes them all, deletes the clone if one was created, and clears the
    ledger's progress fields — leaving the proposal APPROVED and safe to re-run.
    """
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    try:
        row = led.get(ticket_key)
        if row is None:
            raise ExecutionRefused(f"{ticket_key} is not in the ledger")
        if row["state"] == ledger_mod.EXECUTED:
            raise ExecutionRefused(f"{ticket_key} is EXECUTED — use undo(), not cleanup")
        if not row["proposal_id"]:
            raise ExecutionRefused(f"{ticket_key} has no proposal")
        proposal = proposals.load(row["proposal_id"])
        reader = reader or JiraReadClient()
        writer = writer or JiraWriteClient(execute=execute)
        result = ExecutionResult(proposal_id=proposal.proposal_id, ticket=ticket_key,
                                 dry_run=not execute)

        wanted = (proposal.proposed_comment or "").strip()
        mine = [c for c in reader.comments(ticket_key)
                if (c.get("body") or "").strip() == wanted]
        for comment in mine:
            detail = {"comment_id": comment["id"]}
            try:
                writer.delete_comment(ticket_key, comment["id"])
                led.journal(ticket_key, proposal.proposal_id, "cleanup.delete_comment",
                            True, detail)
                result.steps.append(StepResult("cleanup.delete_comment", True, detail))
            except Exception as exc:
                led.journal(ticket_key, proposal.proposal_id, "cleanup.delete_comment",
                            False, {"error": str(exc), **detail})
                result.steps.append(StepResult("cleanup.delete_comment", False, detail,
                                               str(exc)))
                result.ok = False

        if row["clone_key"]:
            detail = {"key": row["clone_key"], "action": recommendations.CLOSE}
            if execute:
                recommendations.recommend_close(
                    row["clone_key"],
                    f"created by a half-finished execution of {ticket_key}",
                    source_ticket=ticket_key)
            led.journal(ticket_key, proposal.proposal_id, "cleanup.recommend_close",
                        True, detail)
            result.steps.append(StepResult("cleanup.recommend_close", True, detail))

        if execute and result.ok:
            led.note_progress(ticket_key, comment_id=None, clone_key=None)
            led.record_error(ticket_key, "partial execution cleaned up; safe to re-run")
        LOG.info("execute.cleanup", ticket=ticket_key, comments=len(mine),
                 ok=result.ok, dry_run=not execute)
        return result
    finally:
        if own_ledger:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.execute",
        description="Execute or undo an approved proposal. Dry run unless --execute.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("proposal_id")
    p_run.add_argument("--execute", action="store_true",
                       help="actually write to Jira (default is a dry run)")
    p_undo = sub.add_parser("undo")
    p_undo.add_argument("ticket")
    p_undo.add_argument("--execute", action="store_true")
    p_epic = sub.add_parser("create-epic",
                            help="create the epic clones hang under (setup, one-off)")
    p_epic.add_argument("--summary", required=True)
    p_epic.add_argument("--execute", action="store_true")
    p_re = sub.add_parser("redescribe",
                          help="rewrite an executed clone's description")
    p_re.add_argument("ticket")
    p_re.add_argument("--execute", action="store_true")
    p_rs = sub.add_parser("restore-description", help="undo of redescribe")
    p_rs.add_argument("ticket")
    p_rs.add_argument("--execute", action="store_true")
    p_close = sub.add_parser("close",
                             help="close a ticket the human approved closing")
    p_close.add_argument("ticket")
    p_close.add_argument("--status")
    p_close.add_argument("--execute", action="store_true")
    p_reopen = sub.add_parser("reopen", help="undo of close")
    p_reopen.add_argument("ticket")
    p_reopen.add_argument("--execute", action="store_true")
    p_clean = sub.add_parser("cleanup",
                             help="undo a half-finished execution so it can re-run")
    p_clean.add_argument("ticket")
    p_clean.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "run":
            res = execute_proposal(args.proposal_id, execute=args.execute)
        elif args.cmd == "create-epic":
            out = create_epic(args.summary, execute=args.execute)
            print(json.dumps(out, indent=2))
            return 0
        elif args.cmd == "redescribe":
            res = redescribe_clone(args.ticket, execute=args.execute)
        elif args.cmd == "restore-description":
            res = restore_description(args.ticket, execute=args.execute)
        elif args.cmd == "close":
            res = close_recommended(args.ticket, execute=args.execute,
                                    status=args.status)
        elif args.cmd == "reopen":
            res = reopen(args.ticket, execute=args.execute)
        elif args.cmd == "cleanup":
            res = cleanup_partial(args.ticket, execute=args.execute)
        else:
            res = undo(args.ticket, execute=args.execute)
    except ExecutionRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    print(json.dumps(res.to_dict(), indent=2))
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
