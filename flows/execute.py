"""Execute — carry out every approved proposal. Human confirm only.

    select_approved -> for each: execute_proposal -> summarise_execution

`execute_proposal` is the single Jira write path (invariant 1): comment ->
clone -> link -> assign -> transition, each step journalled, each one undoable.
The runtime refuses to construct this flow with any trigger but Human(), so an
approval (a label, the MCP tool, the batch file) only queues the work —
`python -m flows run execute --confirm --execute` or the MCP `execute` tool
with confirm=true carries it out.

Without --execute it is a dry run: every step is logged as "would".

Context in (optional): led.
"""
from __future__ import annotations

from core import ledger as ledger_mod, log
from core.flow import JIRA_WRITE, LOCAL, READ, Flow, ForEach, Human, agent

LOG = log.get("batch")


@agent("select_approved", READ)
def select_approved(ctx):
    """Every APPROVED or CORRECTED ticket that has a proposal."""
    led = ctx.get("led")
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        rows = led.by_state(ledger_mod.APPROVED, ledger_mod.CORRECTED)
    finally:
        if own:
            led.close()
    ctx["approved"] = [r["proposal_id"] for r in rows if r["proposal_id"]]


@agent("execute_proposal", JIRA_WRITE)
def execute_one(ctx):
    """The single Jira write path, for one approved proposal."""
    from core.execute import ExecutionRefused, execute_proposal

    pid = ctx["proposal_id"]
    try:
        ctx["result"] = execute_proposal(pid, execute=ctx.execute,
                                         ledger=ctx.get("led")).to_dict()
    except ExecutionRefused as exc:
        LOG.warn("batch.execute_refused", proposal=pid, reason=str(exc))
        ctx["result"] = {"proposal_id": pid, "ok": False, "refused": str(exc)}


EXECUTE_ONE = Flow("execute_one", [execute_one],
                   doc="One approved proposal through execute_proposal.")


@agent("summarise_execution", LOCAL)
def summarise_execution(ctx):
    """One result per proposal, in the order they were executed."""
    ctx["report"] = [r["result"] if "result" in r else
                     {"proposal_id": r["proposal_id"], "ok": False, "error": r.get("_error")}
                     for r in ctx.get("executed") or []]


EXECUTE = Flow(
    "execute",
    [select_approved,
     ForEach("execute_each", "approved", EXECUTE_ONE,
             lambda ctx, pid: {"proposal_id": pid, "led": ctx.get("led")},
             collect="executed", max_workers=1),
     summarise_execution],
    triggers=[Human()],
    doc="Carry out every approved proposal through execute_proposal. Human confirm only.",
)

FLOWS = (EXECUTE,)
