"""SOP lookup for content work, straight out of the indexed Confluence corpus.

The surfaces table names the pages; this fetches them. A worker reads the SOP
for its surface before drafting, and records which ones it read on the task, so
a change can be traced back to the procedure it followed rather than to the
worker's own idea of how Apollo works.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from core import config

DB = config.ROOT / "corpus" / "corpus.db" if hasattr(config, "ROOT") else None

#: Pages that describe content setup generally rather than one surface. Read
#: once by a worker on its first task, and worth re-reading when Apollo changes.
GENERAL = (
    "PM/Apollo Categories Module: Checklist",
    "PM/Onsite A/B Testing",
    "PM/Campaign Team Apollo Features Roadmap",
)


@dataclass
class Sop:
    ref: str
    title: str
    body: str
    url: str

    def excerpt(self, chars: int = 1200) -> str:
        body = " ".join((self.body or "").split())
        return body[:chars] + ("…" if len(body) > chars else "")


def _db_path():
    from pathlib import Path
    here = Path(__file__).resolve().parents[2]
    return here / "corpus" / "corpus.db"


def fetch(ref: str) -> Sop | None:
    """One SOP by its Confluence ref, e.g. 'PM/How to: Category Module'."""
    path = _db_path()
    if not path.exists():
        return None
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            "SELECT ref, title, body, url FROM documents "
            "WHERE source_type='confluence' AND ref = ? LIMIT 1", (ref,)
        ).fetchone()
    finally:
        con.close()
    return Sop(*row) if row else None


def search(query: str, limit: int = 8) -> list[Sop]:
    """Confluence pages matching free text, best first.

    Used when a task names no surface and the lead has to work out which
    module it belongs to.
    """
    path = _db_path()
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT d.ref, d.title, d.body, d.url "
            "FROM docs_fts JOIN documents d ON d.rowid = docs_fts.rowid "
            "WHERE docs_fts MATCH ? AND d.source_type='confluence' "
            "ORDER BY bm25(docs_fts) LIMIT ?", (query, limit)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [Sop(*r) for r in rows]


def for_surface(surface) -> list[Sop]:
    """Every SOP declared for a surface, in the order the table lists them —
    highest authority first. Missing pages are skipped silently; the worker
    reports what it actually read, so a gap shows up there rather than being
    papered over here."""
    out = []
    for ref in surface.sops:
        sop = fetch(ref)
        if sop:
            out.append(sop)
    return out
