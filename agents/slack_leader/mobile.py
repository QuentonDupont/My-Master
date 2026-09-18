"""Slack reply proposals on the phone, and the decisions coming back.

Mirrors agents/jira_leader/mobile.py. The page shows the exact text that would
be posted and who it would be posted as; approving records the decision, and
nothing reaches Slack until the reply is actually sent — by the connector in the
Claude session (connector mode) or by core.slack_execute with the bot token.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, corrections, ledger as ledger_mod, log, slack_proposals

LOG = log.get("slack_mobile")

REVIEWABLE = (ledger_mod.PROPOSED,)


def to_document(proposal: slack_proposals.SlackProposal, stamp: str,
                row: dict | None = None, posts_as: str = "") -> dict:
    d = proposal.to_dict()
    return {
        "proposal_id": d["proposal_id"],
        "channel": d["channel"],
        "channel_name": d["channel_name"],
        "thread_ts": d["thread_ts"],
        "permalink": d["permalink"],
        "asked_by": d["asked_by_name"] or d["asked_by"],
        "question": d["question_restated"],
        "kind": d["kind"],
        "confidence": d["confidence"],
        "reply": d["proposed_reply"],
        "evidence": d["evidence"],
        "flags": d["flags"],
        "posts_as": posts_as,
        "state": (row or {}).get("state", ""),
        "decision": "pending",
        "note": "",
        "edited_reply": "",
        "generated": stamp,
    }


def export(out_dir: Path, posts_as: str = "",
           led: ledger_mod.Ledger | None = None) -> list[str]:
    own = led is None
    led = led or ledger_mod.Ledger()
    slack_led = ledger_mod.SlackLedger(led)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%d %b %H:%M")
        written = []
        states = REVIEWABLE + (ledger_mod.ESCALATED, ledger_mod.APPROVED,
                               ledger_mod.CORRECTED, ledger_mod.EXECUTED)
        for row in slack_led.by_state(*states):
            if not row["proposal_id"]:
                continue
            try:
                proposal = slack_proposals.load(row["proposal_id"])
            except FileNotFoundError:
                continue
            path = out_dir / f"{proposal.proposal_id}.json"
            path.write_text(json.dumps(to_document(proposal, stamp, row, posts_as),
                                       indent=1), encoding="utf-8")
            written.append(str(path))
        LOG.info("slack_mobile.export", documents=len(written))
        return written
    finally:
        if own:
            led.close()


def apply(decisions: list[dict], led: ledger_mod.Ledger | None = None) -> dict:
    """Apply phone decisions. An edited reply is a correction, and is logged."""
    own = led is None
    led = led or ledger_mod.Ledger()
    slack_led = ledger_mod.SlackLedger(led)
    try:
        result = {"approved": [], "corrected": [], "rejected": [], "skipped": [],
                  "errors": []}
        for item in decisions:
            pid = item.get("proposal_id") or item.get("id")
            decision = (item.get("decision") or "pending").lower()
            note = (item.get("note") or "").strip()
            edited = (item.get("edited_reply") or "").strip()

            if decision == "pending":
                result["skipped"].append(pid)
                continue
            try:
                proposal = slack_proposals.load(pid)
            except FileNotFoundError:
                result["errors"].append({"proposal_id": pid, "error": "not in store"})
                continue
            row = slack_led.by_proposal(pid)
            if row is None:
                result["errors"].append({"proposal_id": pid, "error": "not tracked"})
                continue
            if row["state"] not in REVIEWABLE:
                result["errors"].append({"proposal_id": pid,
                                         "error": f"state is {row['state']}"})
                continue

            if decision == "reject":
                corrections.record_rejection(pid, note or "rejected from the phone")
                slack_led.transition(row["thread_key"], ledger_mod.REJECTED)
                result["rejected"].append(pid)
                continue
            if decision != "approve":
                result["errors"].append({"proposal_id": pid,
                                         "error": f"unknown decision {decision!r}"})
                continue

            changed = bool(edited) and edited != proposal.proposed_reply
            if changed:
                corrections.record(pid, "proposed_reply", proposal.proposed_reply,
                                   edited, note or "edited during mobile review")
                proposal.proposed_reply = edited
            problems = slack_proposals.validate(proposal.to_dict())
            if problems:
                result["errors"].append({"proposal_id": pid,
                                         "error": "edited reply is invalid",
                                         "problems": problems})
                continue
            slack_proposals.save(proposal)
            slack_led.transition(row["thread_key"],
                                 ledger_mod.CORRECTED if changed
                                 else ledger_mod.APPROVED)
            (result["corrected"] if changed else result["approved"]).append(pid)
        LOG.info("slack_mobile.apply", **{k: len(v) for k, v in result.items()})
        return result
    finally:
        if own:
            led.close()


def ready_to_post(led: ledger_mod.Ledger | None = None) -> list[dict]:
    """Approved replies waiting to be sent, with everything needed to send one."""
    own = led is None
    led = led or ledger_mod.Ledger()
    slack_led = ledger_mod.SlackLedger(led)
    try:
        out = []
        for row in slack_led.by_state(ledger_mod.APPROVED, ledger_mod.CORRECTED):
            if not row["proposal_id"]:
                continue
            proposal = slack_proposals.load(row["proposal_id"])
            out.append({"proposal_id": proposal.proposal_id,
                        "channel": proposal.channel,
                        "channel_name": proposal.channel_name,
                        "thread_ts": proposal.thread_ts,
                        "permalink": proposal.permalink,
                        "text": proposal.proposed_reply})
        return out
    finally:
        if own:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.slack_leader.mobile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_e = sub.add_parser("export")
    p_e.add_argument("--out", default="review/mobile/slack")
    p_e.add_argument("--posts-as", default="")
    p_a = sub.add_parser("apply")
    p_a.add_argument("path")
    sub.add_parser("ready", help="approved replies waiting to be posted")
    args = ap.parse_args(argv)

    if args.cmd == "export":
        for path in export(Path(args.out), posts_as=args.posts_as):
            print(path)
    elif args.cmd == "apply":
        raw = json.loads(Path(args.path).read_text(encoding="utf-8"))
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        print(json.dumps(apply(items), indent=2))
    else:
        print(json.dumps(ready_to_post(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
