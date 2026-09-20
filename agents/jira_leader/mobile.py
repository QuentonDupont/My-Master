"""The phone bridge: publish the batch for mobile review, apply what comes back.

The page on claude.ai holds one document per proposal and records a decision on
each. It cannot write to Jira — it has no Jira credentials and no execution path.
Decisions come back here, get recorded as corrections, and only then can
`core.execute` act on them.

    python -m agents.jira_leader.mobile export --out <dir>   # docs to seed the page
    python -m agents.jira_leader.mobile apply <decisions.json>

`decisions.json` is what the Artifact db returns: a list (or {"items": [...]})
of {proposal_id, decision, note, assignee_override}.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, corrections, ledger as ledger_mod, log, proposals

LOG = log.get("mobile")

REVIEWABLE = (ledger_mod.PROPOSED,)


#: ledger state -> the decision a review page should show
_DECISIONS = {
    ledger_mod.APPROVED: "approve",
    ledger_mod.CORRECTED: "approve",
    ledger_mod.EXECUTED: "approve",
    ledger_mod.REJECTED: "reject",
}


def _decision_of(row: dict | None) -> str:
    return _DECISIONS.get((row or {}).get("state", ""), "pending")


def to_document(proposal: proposals.Proposal, stamp: str,
                row: dict | None = None) -> dict:
    """One proposal, shaped for the review page."""
    d = proposal.to_dict()
    clone = d["clone"]
    return {
        "ticket": d["ticket"],
        "url": d["ticket_url"],
        "classification": d["classification"],
        "confidence": d["confidence"],
        "requirement": d["requirement_restated"],
        "requester_email": d["requester"]["email"] or "",
        "requester_source": d["requester"]["source"],
        "requester_confidence": d["requester"]["confidence"],
        "flags": d["flags"],
        "comment": d["proposed_comment"],
        "evidence": [{"ref": e["ref"], "why": e["why"]} for e in d["evidence"]],
        "clone": None if not clone else {
            "target": clone["target_project"],
            "issue_type": config.dev_issue_type(),
            "summary": clone["summary"],
            "labels": clone["labels"],
            "priority": clone["priority"],
            "assignee": clone["assignee"],
            "assignee_reason": clone["assignee_reason"],
            "alternates": clone["assignee_alternates"],
        },
        "transition": d["pesd1_transition"] or "",
        # Derived from the ledger, not hardcoded. This used to always say
        # "pending", which was harmless when the page kept decisions in its own
        # store and read them back separately — and wrong the moment anything
        # re-exports on a poll, because every refresh reported an already
        # decided proposal as still waiting.
        "decision": _decision_of(row),
        "note": "",
        "assignee_override": None,
        "decided_at": None,
        "ledger_state": (row or {}).get("state", ""),
        "clone_key": (row or {}).get("clone_key") or "",
        "generated": stamp,
    }


def export(out_dir: Path, led: ledger_mod.Ledger | None = None) -> list[str]:
    """Write one JSON file per reviewable proposal, ready to seed the page."""
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%d %b %H:%M")
        written = []
        states = REVIEWABLE + (ledger_mod.ESCALATED, ledger_mod.DUPLICATE,
                               ledger_mod.APPROVED, ledger_mod.CORRECTED,
                               ledger_mod.EXECUTED)
        for row in led.by_state(*states):
            if not row["proposal_id"]:
                continue
            try:
                proposal = proposals.load(row["proposal_id"])
            except FileNotFoundError:
                continue
            path = out_dir / f"{proposal.proposal_id}.json"
            path.write_text(
                json.dumps(to_document(proposal, stamp, row), indent=1),
                encoding="utf-8")
            written.append(str(path))
        LOG.info("mobile.export", documents=len(written), out=str(out_dir))
        return written
    finally:
        if own:
            led.close()


def recommendation_documents() -> list[dict]:
    """Open "Close as Won't Do" items, shaped for the review page."""
    from core import recommendations

    return [{
        "id": r["id"],
        "ticket": r["ticket"],
        "url": r["ticket_url"],
        "action": r["action"],
        "reason": r["reason"],
        "related": r.get("related") or [],
        "base": config.base_url() + "/browse/",
        "state": r.get("state", "open"),
    } for r in recommendations.load()]


def apply(decisions: list[dict], led: ledger_mod.Ledger | None = None) -> dict:
    """Apply phone decisions. An assignee change is a correction, and is logged."""
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        result = {"approved": [], "corrected": [], "rejected": [], "skipped": [],
                  "errors": []}
        for item in decisions:
            pid = item.get("proposal_id") or item.get("id")
            decision = (item.get("decision") or "pending").lower()
            note = (item.get("note") or "").strip()
            override = item.get("assignee_override")

            if decision == "pending":
                result["skipped"].append(pid)
                continue
            try:
                proposal = proposals.load(pid)
            except FileNotFoundError:
                result["errors"].append({"proposal_id": pid, "error": "not in store"})
                continue
            row = led.by_proposal(pid)
            if row is None:
                result["errors"].append({"proposal_id": pid, "error": "not in ledger"})
                continue
            if row["state"] not in REVIEWABLE:
                result["errors"].append({"proposal_id": pid,
                                         "error": f"ledger state is {row['state']}"})
                continue

            if decision == "reject":
                corrections.record_rejection(pid, note or "rejected from the phone")
                led.transition(row["ticket_key"], ledger_mod.REJECTED)
                result["rejected"].append(pid)
                continue
            if decision != "approve":
                result["errors"].append({"proposal_id": pid,
                                         "error": f"unknown decision {decision!r}"})
                continue

            changed = False
            if override and proposal.clone and override != proposal.clone.assignee:
                corrections.record(pid, "clone.assignee", proposal.clone.assignee,
                                   override, note or "reassigned during mobile review")
                alternates = [a for a in proposal.clone.assignee_alternates
                              if a != override]
                if proposal.clone.assignee and proposal.clone.assignee not in alternates:
                    alternates.insert(0, proposal.clone.assignee)
                corrections.record(pid, "clone.assignee_reason",
                                   proposal.clone.assignee_reason,
                                   note or "chosen by the approver",
                                   "mobile review")
                proposal.clone.assignee = override
                proposal.clone.assignee_reason = note or "chosen by the approver"
                proposal.clone.assignee_alternates = alternates or [override]
                changed = True
            elif note:
                corrections.record(pid, "__note__", None, note, "mobile review")

            problems = proposals.validate(proposal.to_dict())
            if problems:
                result["errors"].append({"proposal_id": pid,
                                         "error": "edited proposal is invalid",
                                         "problems": problems})
                continue
            proposals.save(proposal)
            if changed:
                led.transition(row["ticket_key"], ledger_mod.CORRECTED)
                result["corrected"].append({"proposal_id": pid, "assignee": override})
            else:
                led.transition(row["ticket_key"], ledger_mod.APPROVED)
                result["approved"].append(pid)
        LOG.info("mobile.apply", **{k: len(v) for k, v in result.items()})
        return result
    finally:
        if own:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.mobile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_e = sub.add_parser("export", help="write the documents that seed the page")
    p_e.add_argument("--out", default="review/mobile")
    p_a = sub.add_parser("apply", help="apply decisions taken on the page")
    p_a.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "export":
        for path in export(Path(args.out)):
            print(path)
        recs = recommendation_documents()
        if recs:
            out = Path(args.out) / "recommendations"
            out.mkdir(parents=True, exist_ok=True)
            for rec in recs:
                (out / f"{rec['id'].replace(':', '_').replace(chr(39), '')}.json"
                 ).write_text(json.dumps(rec, indent=1), encoding="utf-8")
                print(out / f"{rec['id'].replace(':', '_').replace(chr(39), '')}.json")
    else:
        raw = json.loads(Path(args.path).read_text(encoding="utf-8"))
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        print(json.dumps(apply(items), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
