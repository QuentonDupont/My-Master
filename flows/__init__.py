"""Every flow in the system, by name.

One module per flow; each defines its own single-purpose agents and the Flow
that chains them. The department packages under agents/ and core/ are the
libraries those agents call — they no longer orchestrate anything themselves.

    python -m flows list
    python -m flows show intake
    python -m flows run intake [--execute]
    python -m flows run execute --confirm --execute
    python -m flows tick           # what launchd runs every minute
    python -m flows status
"""
from __future__ import annotations

import importlib

from core.flow import Flow

#: Module names under flows/, in the order `list` shows them.
MODULES = ("intake", "decisions", "execute", "mirror", "research", "corpus",
           "content", "escalation", "report", "housekeeping", "retriage")


def registry() -> dict[str, Flow]:
    out: dict[str, Flow] = {}
    for mod in MODULES:
        m = importlib.import_module(f"flows.{mod}")
        for flow in getattr(m, "FLOWS", ()):
            if flow.name in out:
                raise RuntimeError(f"duplicate flow name {flow.name}")
            out[flow.name] = flow
    return out


def get(name: str) -> Flow:
    flows = registry()
    if name not in flows:
        raise KeyError(f"no flow named {name!r}; have {sorted(flows)}")
    return flows[name]
