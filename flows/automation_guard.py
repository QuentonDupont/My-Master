"""Automation guard — keep the [PM-Auto] Jira Automation rules inside the monthly limit. Hourly.

    estimate_automation_usage -> guard_automation_usage

The board owner (30 Oct 2026) had nine Jira Automation rules built for PESD1/PRDT and asked
that their usage be watched, that he be told when the site approaches its automation-steps
limit, and that all of them be turned off then. Jira exposes no usage API, so the estimate
lives in ~/pomelo/tools/jira-automations/guard.py: the last exact meter reading plus the
other rules' daily rate plus our rules' footprints in Jira.

The one write is the sanctioned exception in CLAUDE.md invariant 1: it may only switch
rules whose name starts "[PM-Auto]" OFF — never on, never edit, never anything else.
A dry run (the default for manual runs) only estimates.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from core.flow import JIRA_WRITE, READ, Every, Flow, agent

GUARD = pathlib.Path.home() / "pomelo" / "tools" / "jira-automations" / "guard.py"


def _run(*args: str) -> dict:
    out = subprocess.run([sys.executable, str(GUARD), *args], capture_output=True, text=True,
                         timeout=300, check=True).stdout
    return json.loads(out[out.index("{"):])


@agent("estimate_automation_usage", READ)
def estimate_automation_usage(ctx):
    """Estimate the site's automation-steps usage this period (read-only)."""
    ctx["usage"] = _run() if GUARD.exists() else {"level": "unknown", "error": f"{GUARD} missing"}


@agent("guard_automation_usage", JIRA_WRITE)
def guard_automation_usage(ctx):
    """At the warn level alert the owner; at the off level switch every [PM-Auto] rule OFF."""
    usage = ctx["usage"]
    if usage.get("level") in ("warn", "off") and ctx.execute:
        usage = _run("--apply")
    ctx["report"] = {k: usage.get(k) for k in ("estimate", "limit", "pct", "level", "reset", "disabled")}


AUTOMATION_GUARD = Flow(
    "automation-guard",
    [estimate_automation_usage, guard_automation_usage],
    triggers=[Every(3600)],
    doc="Watch [PM-Auto] Jira automation usage; alert near the limit, switch them off at 90%.",
    live_when_scheduled=True,
    timeout_seconds=600,
)

FLOWS = (AUTOMATION_GUARD,)
