"""Approve from Jira, by putting a label on the ticket.

    python -m agents.jira_leader.labels poll
    python -m agents.jira_leader.labels poll --apply

Why a label
-----------
Every other way of approving needs the laptop with the repo on it. A label works
from the Jira mobile app, in a meeting, from anywhere — and it happens where the
work already is, so there is no second place to remember to visit.

The label is read, never written. The ticket keeps it afterwards: the ledger
already refuses to re-review a decided proposal, so nothing needs cleaning up,
and the label stays as a visible record of who decided and when.

    triage-approved   -> approve the proposal for that ticket
    triage-rejected   -> reject it

Both on one ticket is a contradiction, so it is reported and neither is applied.

A rejection wants a reason — the Chief of Staff clusters those into rules, and
"rejected" on its own teaches nothing. The newest comment on the ticket is taken
as the reason, which is what someone rejecting in Jira would naturally write.

Approving here does not write to Jira. It marks the proposal APPROVED, exactly as
the batch file or the panel would; `batch execute --execute` stays the separate,
deliberate step.
"""
from __future__ import annotations

import argparse
import json

from agents.jira_leader import mobile as mobile_mod
from core import config, ledger as ledger_mod, log

LOG = log.get("labels")

APPROVE_LABEL = "triage-approved"
REJECT_LABEL = "triage-rejected"


def _reason_from(reader, ticket: str) -> str:
    """The newest comment, as the rejection reason."""
    try:
        comments = reader.comments(ticket)
    except Exception as exc:
        LOG.warn("labels.comments_failed", ticket=ticket, error=str(exc)[:120])
        return ""
    if not comments:
        return ""
    body = comments[-1].get("body")
    body = body if isinstance(body, str) else str(body)
    return " ".join(body.split())[:500]


def pending(reader, led: ledger_mod.Ledger | None = None) -> list[dict]:
    """Proposals whose ticket now carries a decision label. Pure read."""
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        out = []
        for row in led.by_state(ledger_mod.PROPOSED):
            ticket, pid = row.get("ticket_key"), row.get("proposal_id")
            if not ticket or not pid:
                continue
            try:
                labels = set(reader.issue(
                    ticket, fields="labels")["fields"].get("labels") or [])
            except Exception as exc:
                LOG.warn("labels.read_failed", ticket=ticket, error=str(exc)[:120])
                continue

            approved, rejected = APPROVE_LABEL in labels, REJECT_LABEL in labels
            if approved and rejected:
                out.append({"ticket": ticket, "proposal_id": pid,
                            "decision": "conflict",
                            "note": f"both {APPROVE_LABEL} and {REJECT_LABEL} "
                                    "are set; remove one"})
            elif approved:
                out.append({"ticket": ticket, "proposal_id": pid,
                            "decision": "approve", "note": "approved in Jira"})
            elif rejected:
                reason = _reason_from(reader, ticket)
                out.append({"ticket": ticket, "proposal_id": pid,
                            "decision": "reject",
                            "note": reason or "rejected in Jira, no reason given"})
        return out
    finally:
        if own:
            led.close()


def poll(reader, led: ledger_mod.Ledger | None = None,
         apply: bool = False) -> dict:
    """Find labelled tickets and, with `apply`, record their decisions."""
    found = pending(reader, led)
    decisions = [d for d in found if d["decision"] in ("approve", "reject")]
    conflicts = [d for d in found if d["decision"] == "conflict"]
    result = {"found": len(found), "conflicts": conflicts,
              "decisions": decisions, "applied": None}
    if apply and decisions:
        result["applied"] = mobile_mod.apply(
            [{"proposal_id": d["proposal_id"], "decision": d["decision"],
              "note": d["note"]} for d in decisions], led=led)
    LOG.info("labels.poll", found=len(found), conflicts=len(conflicts),
             applied=bool(apply and decisions))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.labels")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll", help="read decision labels off the board")
    p.add_argument("--apply", action="store_true",
                   help="record the decisions; without it this only reports")
    args = ap.parse_args(argv)

    from core.jira_client import JiraReadClient

    out = poll(JiraReadClient(), apply=args.apply)
    print(json.dumps(out, indent=2))
    if out["conflicts"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
