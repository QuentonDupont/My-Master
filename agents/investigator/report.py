"""The investigation packet — what a person reads to fix the ticket.

Markdown for reading, JSON beside it for tools. It always says which database the
evidence came from, because pre-production is a stale copy (11 Nov 2026: 6 of 33
products looked wrong there and were fine in production).
"""
from __future__ import annotations

import json
import pathlib


def render(inv: dict) -> str:
    out = [f"# Investigation — {inv['ticket']}: {inv['summary']}", ""]
    out.append(f"- **Ticket:** {inv['url']}")
    out.append(f"- **Evidence from:** {inv['db_label']}"
               + ("  ⚠ pre-production is a stale copy — confirm on production before acting" if "pre" in inv["db_label"].lower() else ""))
    ids = ", ".join(f"{k}: {v}" for k, v in inv["entities"].items()) or "none found"
    out.append(f"- **Ids found:** {ids}")
    out.append(f"- **Playbook:** {inv['playbook'] or 'none matched'}"
               + (f" (precedent {inv['precedent']})" if inv.get("precedent") else ""))
    out.append(f"- **Confidence:** {inv['confidence']}")
    out += ["", "## Root cause", inv["root_cause"] or "_Not determined — see questions below._", ""]
    if inv["evidence"]:
        out += ["## Evidence"] + [f"- {e}" for e in inv["evidence"]] + [""]
    if inv["steps"]:
        out += ["## Fix, step by step"] + [f"{i}. {s}" for i, s in enumerate(inv["steps"], 1)] + [""]
    if inv["fix_sql"]:
        out += ["## Fix SQL (for a person to run — never run by the system)", "```sql",
                inv["fix_sql"].strip(), "```", ""]
    if inv["info_needed"]:
        out += ["## Information still needed"] + [f"- {q}" for q in inv["info_needed"]] + [""]
    if inv["code"]:
        out += ["## Code involved"] + [f"- `{c}`" for c in inv["code"]] + [""]
    if inv["queries_run"]:
        out += ["## Queries run (Queries repo `catalog/`)"] + [f"- `{q}`" for q in inv["queries_run"]] + [""]
    if inv.get("errors"):
        out += ["## Could not check"] + [f"- {e}" for e in inv["errors"]] + [""]
    return "\n".join(out)


def write(inv: dict, folder: pathlib.Path) -> pathlib.Path:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    md = folder / f"{inv['ticket']}.md"
    md.write_text(render(inv), encoding="utf-8")
    (folder / f"{inv['ticket']}.json").write_text(json.dumps(inv, indent=1, default=str), encoding="utf-8")
    return md
