"""Re-entry: the human answers an escalated ticket, and it rejoins the flow.

    python -m agents.jira_leader.reentry answer p_0008 --comment "..."
    python -m agents.jira_leader.reentry answer p_0008 --comment-file reply.txt
    python -m agents.jira_leader.reentry list

The problem this closes
-----------------------
ESCALATED is terminal. When the system cannot state a requirement it hands the
ticket to the human, and there is deliberately no route from there to a write —
`execute_proposal` refuses an ESCALATE proposal and `batch apply` only reviews
PROPOSED. That is correct as a default: the system should not talk itself back
into answering something it did not understand.

The cost is that the human's answer never enters the system. They reply in Jira
by hand, `corrections.jsonl` stays empty for that ticket, and the Chief of Staff
learns nothing from the one case where a person had to step in — which is
exactly the case worth learning from. PESD1-10390 sat escalated with the
NetSuite developer's finished answer in a comment the gate cannot read.

What this does
--------------
Takes the human's text, records it as a correction, replaces the proposal's
comment with it, and walks ESCALATED -> CLAIMED -> PROPOSED, which the state
machine already permits (`TRANSITIONS[ESCALATED] == {CLAIMED}`). The ticket is
then reviewable and goes out through the ordinary `batch apply` / `execute`
path. No new write path to Jira is added, and none should be.

What it will not do
-------------------
A never-touch escalation stays escalated (invariant 3). Refunds, payments,
pricing, customer PII, stock adjustments and account access are escalated "with
no comment and no clone, regardless of confidence" — and a human routing their
own answer back through the system is still the system posting it. Those are
answered in Jira by hand, by a person, under their own name.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import corrections, ledger as ledger_mod, log, proposals

LOG = log.get("reentry")

#: escalations that may never be answered through the system (invariant 3)
NEVER_TOUCH_FLAG = "never_touch"

#: marks a proposal whose text a person wrote, for the batch and the audit trail
HUMAN_FLAG = "human_answer"


class ReentryRefused(RuntimeError):
    """The ticket may not rejoin the flow. The message says why."""


def escalated(led: ledger_mod.Ledger | None = None) -> list[dict]:
    """Escalated tickets, and whether each may be answered through the system."""
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        out = []
        for row in led.by_state(ledger_mod.ESCALATED):
            pid = row.get("proposal_id")
            if not pid:
                continue
            try:
                proposal = proposals.load(pid)
            except FileNotFoundError:
                continue
            blocked = [f for f in proposal.flags
                       if f == NEVER_TOUCH_FLAG or f.startswith(f"{NEVER_TOUCH_FLAG}:")]
            out.append({
                "proposal_id": pid,
                "ticket": proposal.ticket,
                "requirement": proposal.requirement_restated,
                "flags": list(proposal.flags),
                "answerable": not blocked,
                "why_not": ", ".join(blocked) if blocked else "",
            })
        return out
    finally:
        if own:
            led.close()


def answer(proposal_id: str, comment: str, *, reason: str = "",
           led: ledger_mod.Ledger | None = None) -> dict:
    """Attach a human's answer to an escalated proposal and make it reviewable."""
    comment = (comment or "").strip()
    if not comment:
        raise ReentryRefused("an answer with no text is not an answer")

    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        proposal = proposals.load(proposal_id)
        row = led.get(proposal.ticket)
        if row is None:
            raise ReentryRefused(f"{proposal.ticket} is not in the ledger")
        if row["state"] != ledger_mod.ESCALATED:
            raise ReentryRefused(
                f"{proposal.ticket} is {row['state']}, not {ledger_mod.ESCALATED}; "
                "re-entry is only for a ticket the system handed to a human")

        blocked = [f for f in proposal.flags
                   if f == NEVER_TOUCH_FLAG or f.startswith(f"{NEVER_TOUCH_FLAG}:")]
        if blocked:
            raise ReentryRefused(
                f"{proposal.ticket} is never-touch ({', '.join(blocked)}); "
                "answer it in Jira yourself, under your own name")

        before = proposal.to_dict()
        proposal.classification = proposals.ANSWERABLE
        proposal.proposed_comment = comment
        proposal.clone = None
        proposal.pesd1_transition = None
        proposal.confidence = 1.0          # a person wrote it; it is not a guess
        proposal.flags = [f for f in proposal.flags if f != "sanity_gate"]
        if HUMAN_FLAG not in proposal.flags:
            proposal.flags.append(HUMAN_FLAG)

        problems = proposals.validate(proposal.to_dict())
        if problems:
            raise ReentryRefused(f"the answered proposal is invalid: {problems}")

        corrections.record_many(
            proposal_id, proposals.diff(before, proposal.to_dict()),
            reason or "answered by the board owner after escalation")
        proposals.save(proposal)

        # The state machine already allows this; nothing drove it before.
        led.transition(proposal.ticket, ledger_mod.CLAIMED)
        led.transition(proposal.ticket, ledger_mod.PROPOSED, proposal_id=proposal_id)

        LOG.info("reentry.answered", ticket=proposal.ticket, proposal=proposal_id,
                 chars=len(comment))
        return {"proposal_id": proposal_id, "ticket": proposal.ticket,
                "state": ledger_mod.PROPOSED, "chars": len(comment)}
    finally:
        if own:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.reentry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="escalated tickets, and which may be answered")
    p_a = sub.add_parser("answer", help="attach an answer and make it reviewable")
    p_a.add_argument("proposal_id")
    src = p_a.add_mutually_exclusive_group(required=True)
    src.add_argument("--comment")
    src.add_argument("--comment-file", help="read the answer from a file, or - for stdin")
    p_a.add_argument("--reason", default="", help="why, for corrections.jsonl")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(escalated(), indent=2, ensure_ascii=False))
        return 0

    if args.comment_file:
        import sys

        text = (sys.stdin.read() if args.comment_file == "-"
                else Path(args.comment_file).read_text(encoding="utf-8"))
    else:
        text = args.comment
    try:
        result = answer(args.proposal_id, text, reason=args.reason)
    except ReentryRefused as exc:
        print(f"refused: {exc}")
        return 2
    print(json.dumps(result, indent=2))
    print("\nnow reviewable — assemble a batch, approve it, then:\n"
          "  python -m agents.jira_leader.batch execute --execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
