"""Keep a PESD1 parent's status in step with its PRDT clone.

The requester watches the PESD1 ticket; the work happens on the PRDT clone. When
those drift, the person who asked is told nothing while their request moves from
To Do to Live. This walks every parent/clone pair — the ledger's, plus open PESD1
tickets linked "is cloned by" a PRDT ticket in Jira itself — and moves the parent
to match. Most of the board was cloned by hand or before the ledger existed, so
the ledger alone never saw it (27 Oct 2026).

Sanctioned exception to invariant 1
-----------------------------------
Invariant 1 says every Jira write goes through `execute_proposal`. This is the
one write that does not, because there is nothing to propose: the decision was
made when the clone was approved, and mirroring is bookkeeping after it. The
exception is deliberately narrow and CLAUDE.md names it:

* transitions only — never a comment, a clone, a link or an assignment
* only a PESD1 parent, only to the status of its PRDT clone — the one recorded
  in the ledger, or the one linked to it in Jira as a clone (Cloners link)
* a parent with several clones moves only when every clone implies the same
  status; when they disagree it is reported
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

#: The Jira link type a clone carries back to the ticket it was cloned from.
CLONE_LINK = "Cloners"

#: Open PESD1 parents with a clone link. Closed parents are left alone.
LINKED_JQL = ('project = PESD1 AND issueLinkType = "{link}" '
              'AND status not in ("Live", "Closed - Won\'t Do")')


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


def linked_clones(reader) -> dict[str, list[str]]:
    """Open PESD1 parents -> the PRDT tickets linked to them as clones in Jira."""
    out: dict[str, list[str]] = {}
    jql = LINKED_JQL.format(link=CLONE_LINK)
    for issue in reader.search(jql, fields=["issuelinks"]):
        for link in issue["fields"].get("issuelinks") or []:
            if (link.get("type") or {}).get("name") != CLONE_LINK:
                continue
            other = link.get("inwardIssue") or link.get("outwardIssue") or {}
            key = other.get("key", "")
            if key.startswith("PRDT-"):
                out.setdefault(issue["key"], []).append(key)
    return out


def pairs(reader, led: ledger_mod.Ledger) -> dict[str, list[str]]:
    """Every parent and its clones: the ledger's executed rows, then Jira's links."""
    out: dict[str, list[str]] = {}
    for row in led.by_state(ledger_mod.EXECUTED):
        parent, clone = row.get("ticket_key"), row.get("clone_key")
        if parent and clone:
            out.setdefault(parent, []).append(clone)
    try:
        for parent, clones in linked_clones(reader).items():
            known = out.setdefault(parent, [])
            known.extend(c for c in clones if c not in known)
    except Exception as exc:
        LOG.warn("mirror.link_search_failed", error=str(exc)[:120])
    return out


def drift(reader, led: ledger_mod.Ledger | None = None) -> list[dict]:
    """Parent/clone pairs whose statuses disagree. Pure read."""
    own = led is None
    led = led or ledger_mod.Ledger()
    allowed = set(config.allowed_projects())
    try:
        out = []
        for parent, clones in pairs(reader, led).items():
            clones = [c for c in clones if c.split("-")[0] in allowed]
            if not clones:
                continue
            try:
                p = reader.issue(parent, fields="status")["fields"]["status"]["name"]
                statuses = {c: reader.issue(c, fields="status")["fields"]["status"]["name"]
                            for c in clones}
            except Exception as exc:
                LOG.warn("mirror.read_failed", parent=parent, error=str(exc)[:120])
                continue

            clone = ", ".join(clones)
            targets = {target_for(s) for s in statuses.values()}
            c = ", ".join(sorted(set(statuses.values())))
            target = targets.pop() if len(targets) == 1 else None
            item = {"parent": parent, "clone": clone,
                    "parent_status": p, "clone_status": c,
                    "target": target, "action": "", "why": ""}
            if targets:                  # more than one implied status
                item["action"] = "report"
                item["why"] = ("clones disagree: "
                               + ", ".join(f"{k} is {v}" for k, v in statuses.items()))
            elif target is None:
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
        return apply_items(drift(reader, led), reader, writer, led, execute=execute)
    finally:
        if own:
            led.close()


def apply_items(items: list[dict], reader, writer, led: ledger_mod.Ledger,
                execute: bool = False) -> list[dict]:
    """Move the parents `drift()` marked "mirror". Each executed move is
    journalled against the parent with the status it came from."""
    done = []
    for item in items:
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
            led.journal(parent, None, "mirror_transition", True,
                        {"from": item["parent_status"], "to": target,
                         "clone": item["clone"]})
            LOG.info("mirror.moved", parent=parent, clone=item["clone"],
                     **{"from": item["parent_status"], "to": target})
        item["executed"] = bool(execute)
        done.append(item)
    return done


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
