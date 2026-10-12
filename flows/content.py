"""Content — draft Marketing & Onsite change plans. On every new content task,
with an hourly sweep as the fallback.

    claim_new_tasks -> for each (3 at a time): draft_plan -> summarise_content

A content task (`python -m agents.marketing_onsite.lead add ...`) emits
`content_task.added`, which wakes this flow. Each task becomes a change plan in
review/content_plans/, or goes to NEEDS_INPUT with the reasons on it — a
drafter never guesses a surface or a market.

Drafting only. Nothing here touches Apollo: applying a plan is a separate,
human-gated step (CLAUDE.md invariant 2, the Marketing & Onsite exception), and
every plan is staged inactive.

Context in (optional): limit.
"""
from __future__ import annotations

from core import log
from core.flow import LOCAL, Every, Flow, ForEach, On, agent

LOG = log.get("marketing_onsite")

LIMIT = 12
DRAFTERS = ("w1", "w2", "w3")


@agent("claim_new_tasks", LOCAL)
def claim_new_tasks(ctx):
    """Take up to 12 NEW content tasks and mark them CLAIMED."""
    from agents.marketing_onsite import tasks

    queue = tasks.in_state(tasks.NEW)[:ctx.get("limit") or LIMIT]
    for t in queue:
        t.state = tasks.CLAIMED
        tasks.save(t)
    ctx["claimed"] = [(i, t.task_id) for i, t in enumerate(queue)]


@agent("draft_plan", LOCAL)
def draft_plan(ctx):
    """One task -> one staged-inactive change plan, or NEEDS_INPUT."""
    from agents.marketing_onsite import tasks, worker

    task = tasks.load(ctx["task_id"])
    worker.draft(task, worker=ctx["drafter"])
    ctx["state"] = tasks.load(ctx["task_id"]).state


DRAFT_ONE = Flow("draft_content", [draft_plan], doc="Draft one content task.")


@agent("summarise_content", LOCAL)
def summarise_content(ctx):
    """How many were drafted, and how many need input from the board owner."""
    from agents.marketing_onsite import tasks

    done = [r for r in ctx.get("drafted") or []]
    drafted = sum(1 for r in done if r.get("state") == tasks.DRAFTED)
    needs = sum(1 for r in done if r.get("state") == tasks.NEEDS_INPUT)
    LOG.info("content.run", drafted=drafted, needs_input=needs)
    ctx["report"] = {"claimed": len(ctx["claimed"]), "drafted": drafted,
                     "needs_input": needs,
                     "tasks": sorted(r["task_id"] for r in done)}


CONTENT = Flow(
    "content",
    [claim_new_tasks,
     ForEach("draft_each", "claimed", DRAFT_ONE,
             lambda ctx, item: {"task_id": item[1],
                                "drafter": DRAFTERS[item[0] % len(DRAFTERS)]},
             collect="drafted", max_workers=len(DRAFTERS)),
     summarise_content],
    triggers=[On("content_task.added"), Every(3600)],
    doc="Draft Marketing & Onsite change plans (staged inactive; never applied here).",
    timeout_seconds=1800,
)

FLOWS = (CONTENT,)
