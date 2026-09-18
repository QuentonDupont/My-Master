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

from core import config, ledger as ledger_mod, log, proposals
from core.jira_client import JiraReadClient, JiraWriteClient

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
                            extra_fields=clone_fields(clone)),
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
            step("delete_clone", lambda: writer.delete_issue(row["clone_key"]),
                 {"key": row["clone_key"]})
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
            detail = {"key": row["clone_key"]}
            try:
                writer.delete_issue(row["clone_key"])
                led.journal(ticket_key, proposal.proposal_id, "cleanup.delete_clone",
                            True, detail)
                result.steps.append(StepResult("cleanup.delete_clone", True, detail))
            except Exception as exc:
                result.steps.append(StepResult("cleanup.delete_clone", False, detail,
                                               str(exc)))
                result.ok = False

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
    p_clean = sub.add_parser("cleanup",
                             help="undo a half-finished execution so it can re-run")
    p_clean.add_argument("ticket")
    p_clean.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "run":
            res = execute_proposal(args.proposal_id, execute=args.execute)
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
