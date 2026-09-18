"""The ONLY Slack write path.

    execute_reply(proposal_id, execute=False)
    undo_reply(thread_key, execute=False)

Same discipline as core/execute.py: a reply is posted only from an approved
proposal, dry run is the default, and every post has its undo. A deleted message
was still delivered, which is why nothing posts without approval.
"""
from __future__ import annotations

import argparse
import json

from core import ledger as ledger_mod, log, slack_proposals
from core.slack_client import SlackWriteClient

LOG = log.get("slack_execute")

EXECUTABLE_STATES = (ledger_mod.APPROVED, ledger_mod.CORRECTED)


class ExecutionRefused(RuntimeError):
    pass


def execute_reply(proposal_id: str, *, execute: bool = False,
                  ledger: ledger_mod.Ledger | None = None,
                  writer: SlackWriteClient | None = None) -> dict:
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    slack_led = ledger_mod.SlackLedger(led)
    try:
        proposal = slack_proposals.load(proposal_id)
        row = slack_led.by_proposal(proposal_id)
        if row is None:
            raise ExecutionRefused(f"{proposal_id} is not against a tracked thread")
        if row["state"] == ledger_mod.EXECUTED:
            raise ExecutionRefused(f"{row['thread_key']} already answered")
        if row["state"] not in EXECUTABLE_STATES:
            raise ExecutionRefused(
                f"{row['thread_key']} is {row['state']}; only "
                f"{'/'.join(EXECUTABLE_STATES)} may be posted")
        problems = slack_proposals.validate(proposal.to_dict())
        if problems:
            raise ExecutionRefused("proposal does not validate: " + "; ".join(problems))
        if proposal.kind == slack_proposals.ESCALATE:
            raise ExecutionRefused("ESCALATE proposals stay silent by design")

        writer = writer or SlackWriteClient(execute=execute)
        if getattr(writer, "execute", False) != bool(execute):
            raise ExecutionRefused("writer execute flag disagrees with --execute")

        result = {"proposal_id": proposal_id, "thread": row["thread_key"],
                  "dry_run": not execute, "ok": True, "reply_ts": None}
        try:
            posted = writer.post_reply(proposal.channel, proposal.thread_ts,
                                       proposal.proposed_reply)
            reply_ts = posted.get("ts") if isinstance(posted, dict) else None
            result["reply_ts"] = reply_ts
            led.journal(row["thread_key"], proposal_id, "slack.reply", True,
                        {"channel": proposal.channel, "chars":
                         len(proposal.proposed_reply), "ts": reply_ts})
            if execute:
                slack_led.transition(row["thread_key"], ledger_mod.EXECUTED,
                                     reply_ts=reply_ts, last_error=None)
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)
            led.journal(row["thread_key"], proposal_id, "slack.reply", False,
                        {"error": str(exc)})
            slack_led.record_error(row["thread_key"], f"reply: {exc}")
        LOG.info("slack_execute.done", thread=row["thread_key"], ok=result["ok"],
                 dry_run=not execute)
        return result
    finally:
        if own_ledger:
            led.close()


def undo_reply(thread_key: str, *, execute: bool = False,
               ledger: ledger_mod.Ledger | None = None,
               writer: SlackWriteClient | None = None) -> dict:
    """Delete a reply we posted. It was still read — this is damage control."""
    own_ledger = ledger is None
    led = ledger or ledger_mod.Ledger()
    slack_led = ledger_mod.SlackLedger(led)
    try:
        row = slack_led.get(thread_key)
        if row is None:
            raise ExecutionRefused(f"{thread_key} is not tracked")
        if row["state"] != ledger_mod.EXECUTED or not row["reply_ts"]:
            raise ExecutionRefused(f"{thread_key} has no posted reply to undo")
        writer = writer or SlackWriteClient(execute=execute)
        result = {"thread": thread_key, "dry_run": not execute, "ok": True}
        try:
            writer.delete_message(row["channel"], row["reply_ts"])
            led.journal(thread_key, row["proposal_id"], "slack.undo", True,
                        {"ts": row["reply_ts"]})
            if execute:
                slack_led.transition(thread_key, ledger_mod.ROLLED_BACK,
                                     reply_ts=None,
                                     last_error="reply deleted by undo")
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)
        return result
    finally:
        if own_ledger:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.slack_execute",
        description="Post or delete a Slack reply. Dry run unless --execute.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("proposal_id")
    p_run.add_argument("--execute", action="store_true")
    p_undo = sub.add_parser("undo")
    p_undo.add_argument("thread_key")
    p_undo.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    try:
        out = (execute_reply(args.proposal_id, execute=args.execute)
               if args.cmd == "run"
               else undo_reply(args.thread_key, execute=args.execute))
    except ExecutionRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
