"""Keep a PESD1 parent's status in step with its PRDT clone.

The requester watches the PESD1 ticket; the work happens on the PRDT clone. When
those drift, the person who asked is told nothing while their request moves from
To Do to Live. This walks the ledger's parent/clone pairs and moves the parent to
match.

Sanctioned exception to invariant 1
-----------------------------------
Invariant 1 says every Jira write goes through `execute_proposal`. This is the
one write that does not, because there is nothing to propose: the decision was
made when the clone was approved, and mirroring is bookkeeping after it. The
exception is deliberately narrow and CLAUDE.md names it:

* transitions only — never a comment, a clone, a link or an assignment
* only a PESD1 parent, only to the status of the PRDT clone recorded in the
  ledger for that parent
* forward only, along the declared order, so a parent is never dragged backwards
* never to a closing status: invariant 9 keeps closing a human decision, so
  "Closed - Won't Do" on the clone is reported, not mirrored
* every move is recorded with the status it came from, so `undo` can reverse it

What the two boards do not share
--------------------------------
PESD1 has no "Ready For QA" and no "Unassigned". A clone in QA is still in
flight, so the parent reads "In progress"; an unassigned clone is back in the
backlog, so the parent reads "To Do".

Workflows also restrict which transitions are available from a given status —
from "Waiting Support" a PESD1 ticket cannot reach "In progress" directly. When
the target is not offered, this reports the gap instead of forcing a path
through statuses nobody asked for.
"""
from __future__ import annotations

import argparse
import json

from core import config, ledger as ledger_mod, log

LOG = log.get("status_mirror")

#: PRDT status -> PESD1 status. Absent means "do not mirror".
STATUS_MAP = {
    "To Do": "To Do",
    "Unassigned": "To Do",
    "In progress": "In progress",
    "Ready For QA": "In progress",
    "Ready For Code Review": "Ready For Code Review",
    "Ready To Release": "Ready To Release",
    "Live": "Live",
    "Blocked": "Blocked",
}

#: How far through the workflow each PESD1 status is. Mirroring only moves up.
#: "Blocked" sits outside the ladder: it is a state, not a stage.
ORDER = {
    "Waiting for customer": 0,
    "Waiting Support": 1,
    "To Do": 2,
    "In progress": 3,
    "Ready For Code Review": 4,
    "Ready To Release": 5,
    "Live": 6,
}

#: Never mirrored. Closing is a human decision (invariant 9).
NEVER_MIRROR = {"Closed - Won't Do"}


def target_for(clone_status: str) -> str | None:
    """The PESD1 status a clone in `clone_status` implies, or None."""
    if clone_status in NEVER_MIRROR:
        return None
    return STATUS_MAP.get(clone_status)


def is_forward(current: str, target: str) -> bool:
    """Would this move the parent onward? Blocked may be entered from anywhere."""
    if current == target:
        return False
    if target == "Blocked":
        return current != "Blocked"
    if current == "Blocked":
        return True                      # leaving Blocked is always progress
    here, there = ORDER.get(current), ORDER.get(target)
    if here is None or there is None:
        return False
    return there > here


def drift(reader, led: ledger_mod.Ledger | None = None) -> list[dict]:
    """Parent/clone pairs whose statuses disagree. Pure read."""
    own = led is None
    led = led or ledger_mod.Ledger()
    allowed = set(config.allowed_projects())
    try:
        out = []
        for row in led.by_state(ledger_mod.EXECUTED):
            parent, clone = row.get("ticket_key"), row.get("clone_key")
            if not parent or not clone:
                continue
            if clone.split("-")[0] not in allowed:
                continue
            try:
                p = reader.issue(parent, fields="status")["fields"]["status"]["name"]
                c = reader.issue(clone, fields="status")["fields"]["status"]["name"]
            except Exception as exc:
                LOG.warn("mirror.read_failed", parent=parent, error=str(exc)[:120])
                continue

            target = target_for(c)
            item = {"parent": parent, "clone": clone,
                    "parent_status": p, "clone_status": c,
                    "target": target, "action": "", "why": ""}
            if target is None:
                item["action"] = "report"
                item["why"] = (f"{c} is not mirrored"
                               + (" — closing is a human decision"
                                  if c in NEVER_MIRROR else " — no PESD1 equivalent"))
            elif p == target:
                item["action"] = "none"
                item["why"] = "already in step"
            elif not is_forward(p, target):
                item["action"] = "report"
                item["why"] = f"would move {parent} backwards, {p} -> {target}"
            else:
                item["action"] = "mirror"
                item["why"] = f"{clone} is {c}"
            out.append(item)
        return out
    finally:
        if own:
            led.close()


def apply(reader, writer, led: ledger_mod.Ledger | None = None,
          execute: bool = False) -> list[dict]:
    """Move each drifted parent onward. `execute=False` reports what it would do."""
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        done = []
        for item in drift(reader, led):
            if item["action"] != "mirror":
                continue
            parent, target = item["parent"], item["target"]
            available = {t["to"]["name"]: t["name"]
                         for t in reader.transitions(parent)}
            if target not in available:
                item["action"] = "report"
                item["why"] = (f"{parent} cannot reach {target!r} from "
                               f"{item['parent_status']!r}; offered: "
                               f"{sorted(available)}")
                done.append(item)
                continue
            item["from_status"] = item["parent_status"]
            if execute:
                writer.transition(parent, target)
                LOG.info("mirror.moved", parent=parent, clone=item["clone"],
                         **{"from": item["parent_status"], "to": target})
            item["executed"] = bool(execute)
            done.append(item)
        return done
    finally:
        if own:
            led.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.status_mirror")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report", help="show every parent/clone pair and any drift")
    p_a = sub.add_parser("apply", help="move drifted parents onward")
    p_a.add_argument("--execute", action="store_true",
                     help="actually transition; without it this is a dry run")
    args = ap.parse_args(argv)

    from core.jira_client import JiraReadClient

    reader = JiraReadClient()
    if args.cmd == "report":
        print(json.dumps(drift(reader), indent=2))
        return 0

    from core.jira_client import JiraWriteClient

    writer = JiraWriteClient(execute=args.execute)
    print(json.dumps(apply(reader, writer, execute=args.execute), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
