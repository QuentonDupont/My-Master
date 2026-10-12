"""Corpus — keep the Historian's index fresh.

corpus      every 2 hours
    export_pesd1 -> export_prdt -> build_index

    Exports tickets *updated* in the last 3 days (not created — a ticket opened
    months ago and resolved yesterday has an old created date forever) and merges
    them into corpus/raw/, then rebuilds the SQLite index. One project's export
    failing never stops the other, or the reindex.

confluence  weekly, Mondays 07:00
    export_confluence -> build_index

    Confluence changes far more slowly than tickets, so it is pulled weekly.

Both read Jira/Confluence and write only corpus/. Context in (optional):
updated_since_days.
"""
from __future__ import annotations

from core.flow import LOCAL, Daily, Every, Flow, Step, agent

UPDATED_SINCE_DAYS = 3  # generous overlap against the 2h interval


def _export(project: str):
    def run(ctx):
        from corpus import export

        days = ctx.get("updated_since_days") or UPDATED_SINCE_DAYS
        ctx[f"exported_{project}"] = export.export_jira(
            project, updated_since_days=days, merge=True)
    run.__doc__ = f"Merge {project} tickets updated in the last few days into corpus/raw/."
    return run


export_pesd1 = agent("export_pesd1", LOCAL)(_export("PESD1"))
export_prdt = agent("export_prdt", LOCAL)(_export("PRDT"))


@agent("export_confluence", LOCAL)
def export_confluence(ctx):
    """Pull the configured Confluence spaces into corpus/raw/."""
    from corpus import export

    ctx["exported_confluence"] = export.export_confluence()


@agent("build_index", LOCAL)
def build_index(ctx):
    """Rebuild the SQLite FTS index from corpus/raw/ and the SOP library."""
    from corpus import index

    ctx["report"] = {k: v for k, v in ctx.data.items() if k.startswith("exported_")}
    ctx["report"]["index"] = index.build()


CORPUS = Flow(
    "corpus",
    [Step(export_pesd1, on_error="continue"), Step(export_prdt, on_error="continue"),
     build_index],
    triggers=[Every(7200)],
    doc="Merge recently updated PESD1/PRDT tickets into the corpus and reindex.",
    timeout_seconds=3600,
)

CONFLUENCE = Flow(
    "confluence",
    [export_confluence, build_index],
    triggers=[Daily("07:00", weekdays=(0,))],
    doc="Weekly pull of the configured Confluence spaces, then reindex.",
    timeout_seconds=3600,
)

FLOWS = (CORPUS, CONFLUENCE)
