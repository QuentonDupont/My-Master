"""Live ticket status for a Slack reply — the Jira Leader answering for the bot.

When someone tags the board owner about a ticket they raised, the thing they
actually want is almost never a runbook link. It is: *where is my ticket, who has
it, and did anything happen*. The Historian answers from history; this answers
from now.

Two sources, cheapest first:

1. the **ledger** — already knows every ticket the system triaged, its state and
   the PRDT clone it was cloned into. No network, always available.
2. **Jira**, read-only — current status, assignee and summary.

Jira is optional on purpose. A Slack reply is worth sending with the ledger's
half of the answer if the network or the token is unavailable, and
`lookup()` degrades instead of raising.

Scope: `config.allowed_projects()` only, enforced by the caller and again here —
PESD1 and PRDT, nothing else (CLAUDE.md, boards in scope).
"""
from __future__ import annotations

from core import config, ledger as ledger_mod, log

LOG = log.get("slack_status")

#: how many named tickets one reply will talk about
MAX_TICKETS = 2


def _from_ledger(key: str, led: ledger_mod.Ledger | None) -> dict:
    if led is None:
        return {}
    try:
        row = led.get(key)
    except Exception as exc:  # a broken ledger must not stop a reply
        LOG.warn("slack_status.ledger_failed", ticket=key, error=str(exc))
        return {}
    if not row:
        return {}
    return {"ledger_state": row.get("state") or "",
            "clone_key": row.get("clone_key") or ""}


def _from_jira(key: str, reader) -> dict:
    if reader is None:
        return {}
    try:
        issue = reader.issue(key, fields="summary,status,assignee,updated")
    except Exception as exc:  # offline, 404, no token — all the same here
        LOG.warn("slack_status.jira_failed", ticket=key, error=str(exc))
        return {}
    fields = issue.get("fields") or {}
    assignee = fields.get("assignee") or {}
    return {
        "summary": (fields.get("summary") or "").strip(),
        "status": ((fields.get("status") or {}).get("name") or "").strip(),
        "assignee": (assignee.get("displayName") or "").strip(),
    }


def lookup(keys: list[str], *, ledger: ledger_mod.Ledger | None = None,
           reader=None) -> list[dict]:
    """What we can say right now about each named ticket, best effort."""
    allowed = set(config.allowed_projects())
    out: list[dict] = []
    seen: set[str] = set()
    for key in keys:
        key = key.upper()
        if key in seen or key.split("-")[0] not in allowed:
            continue
        seen.add(key)
        found = {"key": key}
        found.update(_from_ledger(key, ledger))
        found.update(_from_jira(key, reader))
        if len(found) > 1:          # anything beyond the key itself
            out.append(found)
        if len(out) >= MAX_TICKETS:
            break
    return out


def sentence(found: dict) -> str:
    """One line a requester can act on. Never leaks a board out of scope."""
    key = found["key"]
    status = found.get("status")
    assignee = found.get("assignee")
    clone = found.get("clone_key")

    if status:
        line = f"{key} is *{status}*"
        if assignee:
            line += f", with {assignee}"
        line += "."
    else:
        line = f"{key} is open."

    if clone and clone.split("-")[0] in set(config.allowed_projects()):
        line += f" The development work is tracked in {clone}."
    return line


def paragraph(found: list[dict]) -> str:
    """The status block that leads a reply, or "" when nothing is known."""
    lines = [sentence(f) for f in found]
    return "\n".join(lines) if lines else ""
