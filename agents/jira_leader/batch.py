"""Approval batches — how the human sees the work and answers it.

`assemble` writes two files into review/:
  * batch_<ts>.md   — what you read
  * batch_<ts>.json — what you edit: set "decision" per item, edit any field
                      of "proposal" in place, then run `apply`.

`apply` diffs each edited proposal against the stored one, appends every change
to knowledge/corrections.jsonl and moves the ledger to APPROVED / CORRECTED /
REJECTED. It never writes to Jira — that is `core.execute`, and only for items
this step has already approved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core import config, corrections, ledger as ledger_mod, log, proposals

LOG = log.get("batch")

REVIEWABLE = (ledger_mod.PROPOSED,)
INFORMATIONAL = (ledger_mod.ESCALATED, ledger_mod.DUPLICATE)
DECISIONS = ("pending", "approve", "reject", "informational")


def _ts() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M")


def _load(proposal_id: str | None) -> proposals.Proposal | None:
    if not proposal_id:
        return None
    try:
        return proposals.load(proposal_id)
    except FileNotFoundError:
        return None


def assemble(led: ledger_mod.Ledger | None = None) -> dict:
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        rows = led.by_state(*REVIEWABLE, *INFORMATIONAL)
        items = []
        for row in rows:
            proposal = _load(row["proposal_id"])
            if proposal is None:
                continue
            items.append({
                "decision": "pending" if row["state"] in REVIEWABLE else "informational",
                "reason": "",
                "ledger_state": row["state"],
                "proposal": proposal.to_dict(),
            })
        items.sort(key=lambda i: (i["decision"] != "pending",
                                  -float(i["proposal"]["confidence"])))
        ts = _ts()
        config.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        json_path = config.REVIEW_DIR / f"batch_{ts}.json"
        md_path = config.REVIEW_DIR / f"batch_{ts}.md"
        json_path.write_text(json.dumps({"generated": ts, "items": items}, indent=2),
                             encoding="utf-8")
        md_path.write_text(render(items, ts), encoding="utf-8")
        LOG.info("batch.assembled", items=len(items), path=str(json_path))
        return {"json": str(json_path), "md": str(md_path), "items": len(items)}
    finally:
        if own:
            led.close()


def render(items: list[dict], ts: str) -> str:
    pending = [i for i in items if i["decision"] == "pending"]
    info = [i for i in items if i["decision"] != "pending"]
    out = [f"# Approval batch {ts}", "",
           f"{len(pending)} awaiting your decision, {len(info)} for information.", "",
           "Edit `batch_%s.json`: set `decision` to `approve` or `reject`, change any "
           "field of `proposal` in place, then:" % ts, "",
           "```", f"python -m agents.jira_leader.batch apply review/batch_{ts}.json",
           "python -m agents.jira_leader.batch execute          # dry run",
           "python -m agents.jira_leader.batch execute --execute", "```", ""]

    for item in pending:
        out += _render_item(item)
    if info:
        out += ["---", "", "## For information — no action proposed", ""]
        for item in info:
            out += _render_item(item, brief=True)
    return "\n".join(out)


def _render_item(item: dict, brief: bool = False) -> list[str]:
    p = item["proposal"]
    req = p["requester"]
    out = [f"## {p['ticket']} — {p['classification']} ({p['confidence']:.2f}) "
           f"`{p['proposal_id']}`", "",
           f"<{p['ticket_url']}>", "",
           f"**Requirement:** {p['requirement_restated']}", "",
           f"**Requester:** {req['email'] or 'unknown (manual lookup needed)'} "
           f"— {req['source']}, {req['confidence']} confidence"]
    if req["confidence"] == "medium" and req["matched_row"]:
        out += ["", "```json", json.dumps(req["matched_row"], indent=2), "```"]
    if p["flags"]:
        out += ["", f"**Flags:** {', '.join(p['flags'])}"]
    if p["evidence"]:
        out += ["", "**Evidence:**"]
        out += [f"- `{e['ref']}` ({e['type']}) — {e['why']}" for e in p["evidence"]]
    if p["proposed_comment"]:
        out += ["", "**Comment to post on " + p["ticket"] + ":**", "",
                "> " + p["proposed_comment"].replace("\n", "\n> ")]
    if p["clone"] and not brief:
        c = p["clone"]
        out += ["", f"**Clone into {c['target_project']}:** {c['summary']}", "",
                f"- Assignee: **{c['assignee']}** — {c['assignee_reason']}",
                f"- Alternates: {', '.join(c['assignee_alternates'])}",
                f"- Labels: {', '.join(c['labels']) or '—'} · Priority: {c['priority']}",
                f"- Link: {c['link_type']} · PESD1 moves to: {p['pesd1_transition']}",
                "", "<details><summary>Clone description</summary>", "",
                "```", c["description"], "```", "", "</details>"]
    out += ["", "---", ""]
    return out


# -- applying the human's decisions -----------------------------------------
def apply(batch_path: Path | str, led: ledger_mod.Ledger | None = None) -> dict:
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        data = json.loads(Path(batch_path).read_text(encoding="utf-8"))
        result = {"approved": [], "corrected": [], "rejected": [], "skipped": [],
                  "errors": []}
        for item in data.get("items", []):
            decision = (item.get("decision") or "pending").lower()
            edited = item["proposal"]
            pid = edited["proposal_id"]
            stored = _load(pid)
            if stored is None:
                result["errors"].append({"proposal_id": pid, "error": "not in store"})
                continue
            row = led.by_proposal(pid)
            if row is None:
                result["errors"].append({"proposal_id": pid, "error": "not in ledger"})
                continue

            if decision in ("pending", "informational"):
                result["skipped"].append(pid)
                continue

            if row["state"] not in REVIEWABLE:
                result["errors"].append(
                    {"proposal_id": pid,
                     "error": f"ledger state is {row['state']}, not reviewable"})
                continue

            if decision == "reject":
                corrections.record_rejection(pid, item.get("reason", ""))
                led.transition(row["ticket_key"], ledger_mod.REJECTED)
                result["rejected"].append(pid)
                continue

            if decision != "approve":
                result["errors"].append({"proposal_id": pid,
                                         "error": f"unknown decision {decision!r}"})
                continue

            changes = proposals.diff(stored.to_dict(), edited)
            if changes:
                problems = proposals.validate(edited)
                if problems:
                    result["errors"].append({"proposal_id": pid,
                                             "error": "edited proposal is invalid",
                                             "problems": problems})
                    continue
                corrections.record_many(pid, changes, item.get("reason", ""))
                proposals.save(proposals.Proposal.from_dict(edited))
                led.transition(row["ticket_key"], ledger_mod.CORRECTED)
                result["corrected"].append({"proposal_id": pid,
                                            "fields": [c[0] for c in changes]})
            else:
                led.transition(row["ticket_key"], ledger_mod.APPROVED)
                result["approved"].append(pid)
        LOG.info("batch.applied", **{k: len(v) for k, v in result.items()})
        return result
    finally:
        if own:
            led.close()


def execute_approved(*, execute: bool = False,
                     led: ledger_mod.Ledger | None = None) -> list[dict]:
    """Hand every approved/corrected proposal to the single write path."""
    from core.execute import ExecutionRefused, execute_proposal

    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        results = []
        for row in led.by_state(ledger_mod.APPROVED, ledger_mod.CORRECTED):
            pid = row["proposal_id"]
            if not pid:
                continue
            try:
                res = execute_proposal(pid, execute=execute, ledger=led)
                results.append(res.to_dict())
            except ExecutionRefused as exc:
                LOG.warn("batch.execute_refused", proposal=pid, reason=str(exc))
                results.append({"proposal_id": pid, "ok": False, "refused": str(exc)})
        return results
    finally:
        if own:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.batch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("assemble", help="write a review batch from PROPOSED tickets")
    p_apply = sub.add_parser("apply", help="apply the decisions in an edited batch")
    p_apply.add_argument("path")
    p_one = sub.add_parser("approve", help="approve a single proposal by id")
    p_one.add_argument("proposal_id")
    p_rej = sub.add_parser("reject", help="reject a single proposal by id")
    p_rej.add_argument("proposal_id")
    p_rej.add_argument("--reason", required=True)
    p_exec = sub.add_parser("execute", help="execute everything approved")
    p_exec.add_argument("--execute", action="store_true",
                        help="actually write to Jira (default is a dry run)")
    args = ap.parse_args(argv)

    with ledger_mod.Ledger() as led:
        if args.cmd == "assemble":
            print(json.dumps(assemble(led), indent=2))
        elif args.cmd == "apply":
            print(json.dumps(apply(args.path, led), indent=2))
        elif args.cmd == "approve":
            row = led.by_proposal(args.proposal_id)
            if not row:
                print(f"no ledger row for {args.proposal_id}")
                return 2
            led.transition(row["ticket_key"], ledger_mod.APPROVED)
            print(f"{args.proposal_id} APPROVED")
        elif args.cmd == "reject":
            row = led.by_proposal(args.proposal_id)
            if not row:
                print(f"no ledger row for {args.proposal_id}")
                return 2
            corrections.record_rejection(args.proposal_id, args.reason)
            led.transition(row["ticket_key"], ledger_mod.REJECTED)
            print(f"{args.proposal_id} REJECTED")
        else:
            print(json.dumps(execute_approved(execute=args.execute, led=led), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
