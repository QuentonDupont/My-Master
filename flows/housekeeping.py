"""Housekeeping — keep logs and run history bounded. Hourly.

    rotate_logs -> prune_run_history

rotate_logs truncates any log over 5 MB IN PLACE to its last 1 MB, the same
way tools/rotate_logs.sh does (no file handle anywhere is held open across
writes, so rewriting the inode is safe; a reader skips the one partial line
the cut can leave). It now also covers logs/flows/, where each flow's process
output goes.

prune_run_history drops flow_runs rows older than 14 days. The event journal
is kept: it is small, and it is the audit trail of every state change.
"""
from __future__ import annotations

import datetime as dt

from core import config
from core.flow import LOCAL, Every, Flow, agent

MAX_BYTES = 5_000_000
KEEP_BYTES = 1_000_000
KEEP_RUN_DAYS = 14


@agent("rotate_logs", LOCAL)
def rotate_logs(ctx):
    """Truncate oversized logs in place, keeping their most recent tail."""
    max_bytes = ctx.get("max_bytes") or MAX_BYTES
    keep = ctx.get("keep_bytes") or KEEP_BYTES
    truncated = []
    for pattern in ("*.jsonl", "*.out", "*.err"):
        for path in config.LOG_DIR.rglob(pattern):
            size = path.stat().st_size
            if size <= max_bytes:
                continue
            with path.open("r+b") as fh:
                fh.seek(size - keep)
                tail = fh.read()
                fh.seek(0)
                fh.write(tail)
                fh.truncate()
            truncated.append({"file": str(path.relative_to(config.LOG_DIR)),
                              "from": size, "to": len(tail)})
    ctx["truncated"] = truncated


@agent("prune_run_history", LOCAL)
def prune_run_history(ctx):
    """Forget flow runs older than two weeks."""
    from core import scheduler

    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(days=ctx.get("keep_run_days") or KEEP_RUN_DAYS)
              ).isoformat(timespec="seconds")
    with scheduler.Store() as store:
        cur = store.conn.execute("DELETE FROM flow_runs WHERE started < ?", (cutoff,))
        pruned = cur.rowcount
    ctx["report"] = {"truncated": ctx["truncated"], "runs_pruned": pruned}


HOUSEKEEPING = Flow(
    "housekeeping",
    [rotate_logs, prune_run_history],
    triggers=[Every(3600)],
    doc="Cap log sizes and prune old flow run history.",
    timeout_seconds=600,
)

FLOWS = (HOUSEKEEPING,)
