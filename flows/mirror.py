"""Mirror — keep each PESD1 parent's status in step with its PRDT clone.
Every 5 minutes, and whenever a proposal is executed.

    find_drift -> mirror_parent_status -> report_mirror

The one sanctioned unattended Jira write (CLAUDE.md invariant 1, status
mirroring, 20 Sep 2026): transitions only, PESD1 parents only, forward only,
never into a closing status, never forced. Every move is journalled against
the parent with the status it came from. The rules live in core/status_mirror.py.

Context in (optional): reader, writer, led.
"""
from __future__ import annotations

from core import ledger as ledger_mod
from core.flow import JIRA_WRITE, LOCAL, READ, Every, Flow, On, agent


@agent("find_drift", READ)
def find_drift(ctx):
    """Parent/clone pairs whose statuses disagree, and what to do about each."""
    from core import status_mirror

    if ctx.get("reader") is None:
        from core.jira_client import JiraReadClient
        ctx["reader"] = JiraReadClient()
    if ctx.get("led") is None:
        ctx["led"], ctx["_own_led"] = ledger_mod.Ledger(), True
    ctx["drift"] = status_mirror.drift(ctx["reader"], ctx["led"])


@agent("mirror_parent_status", JIRA_WRITE)
def mirror_parent_status(ctx):
    """Move each drifted parent forward to its clone's status."""
    from core import status_mirror

    if ctx.get("writer") is None:
        from core.jira_client import JiraWriteClient
        ctx["writer"] = JiraWriteClient(execute=ctx.execute)
    ctx["moved"] = status_mirror.apply_items(ctx["drift"], ctx["reader"], ctx["writer"],
                                             ctx["led"], execute=ctx.execute)


@agent("report_mirror", LOCAL)
def report_mirror(ctx):
    """What moved, and what needs a human (closing, backwards, not offered)."""
    reported = [d for d in ctx["drift"] if d["action"] == "report"]
    ctx["report"] = {"moved": ctx["moved"], "needs_human": reported}


def _close(ctx) -> None:
    if ctx.get("_own_led"):
        ctx["led"].close()


MIRROR = Flow(
    "mirror",
    [find_drift, mirror_parent_status, report_mirror],
    triggers=[Every(300), On("ticket.executed")],
    doc="Move PESD1 parents forward to match their PRDT clone (sanctioned exception).",
    live_when_scheduled=True,
    cleanup=_close,
    timeout_seconds=600,
)

FLOWS = (MIRROR,)
