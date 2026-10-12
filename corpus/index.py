"""SQLite FTS5 corpus — the Historian's substrate.

One `documents` row per retrievable thing: a Jira ticket, a Confluence page, an
approved SOP, a GitHub commit/PR. Ranking is BM25, re-weighted by source so the
SOP library outranks raw ticket history (CLAUDE.md, learning loop).

    python -m corpus.index build          # (re)build from corpus/raw/*.jsonl
    python -m corpus.index search "text"  # sanity-check retrieval
    python -m corpus.index stats
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from core import config, log

LOG = log.get("corpus")

RAW_DIR = config.CORPUS_DIR / "raw"

#: source weight — SOPs outrank Confluence, which outranks raw tickets.
SOURCE_WEIGHT = {"sop": 2.0, "confluence": 1.4, "jira": 1.0, "github": 0.8}
#: A sprawling page ("2019 - PM Daily Notes") contains a bit of everything and
#: would otherwise match every query. Documents far longer than a typical ticket
#: are damped, not excluded.
REFERENCE_TERMS = 120
LENGTH_DAMPING = 0.3

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id       TEXT PRIMARY KEY,
  source_type  TEXT NOT NULL,      -- jira | confluence | sop | github
  ref          TEXT NOT NULL,      -- ticket key, page id, sha, pr number
  project      TEXT,
  status       TEXT,
  resolution   TEXT,
  created      TEXT,
  updated      TEXT,
  reporter     TEXT,
  assignee     TEXT,
  labels       TEXT,               -- json list
  components   TEXT,               -- json list
  title        TEXT NOT NULL,
  body         TEXT NOT NULL,
  url          TEXT,
  links        TEXT               -- json: [{key, project, status, type}]
);
CREATE INDEX IF NOT EXISTS documents_type_idx ON documents(source_type);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents(status);
CREATE INDEX IF NOT EXISTS documents_ref_idx ON documents(ref);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, body, doc_id UNINDEXED, tokenize='porter unicode61'
);

-- How many documents each term appears in. Rare words ("racha", "happitat")
-- carry the meaning; common ones ("add", "new", "please") do not, and counting
-- every shared term equally made two tickets about the same shop look unrelated.
CREATE TABLE IF NOT EXISTS term_df (
  term TEXT PRIMARY KEY,
  df   INTEGER NOT NULL
);
"""

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "these", "those",
    "as", "at", "by", "from", "we", "i", "you", "he", "she", "they", "please", "hi",
    "hello", "dear", "team", "thanks", "thank", "regards", "kindly", "can", "could",
    "would", "should", "not", "no", "yes", "has", "have", "had", "do", "does", "did",
    "there", "here", "when", "what", "which", "who", "how", "why", "our", "your",
    # Words every second ticket on a support board carries. They describe that
    # something was raised, never what it was about, and two tickets sharing
    # only these are not related.
    "issue", "issues", "problem", "problems", "request", "requests", "requested",
    "ticket", "tickets", "support", "critical", "urgent", "asap", "need", "needs",
    "help", "update", "updated", "check", "checking", "confirm", "pls", "hello",
    "dear", "regards", "morning", "afternoon", "sorry", "sir", "madam", "khun",
    "am", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it",
    "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we", "id",
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")
#: Links are not subject matter. Tokenising them matched every ticket carrying a
#: Google Drive attachment to every other one, on "drivesdk" and "usp".
URL_RE = re.compile(r"https?://\S+|www\.\S+")


def singular(term: str) -> str:
    """Crude but effective plural folding: locations -> location.

    Without it "Add two new locations" never matches "New Retail Location",
    which is the page that answers it.
    """
    if len(term) > 3 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("ses"):
        return term[:-2]
    if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        return term[:-1]
    return term


def keywords(text: str, limit: int = 18) -> list[str]:
    """Content words, most frequent first — used to build the FTS query."""
    counts: dict[str, int] = {}
    for tok in TOKEN_RE.findall(URL_RE.sub(" ", text or "")):
        t = singular(tok.lower())
        # Two letters is not too short here: PO, NS, WH, IR and SG are the
        # subject of half this board's tickets.
        if t in STOPWORDS or len(t) < 2:
            continue
        counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in ranked[:limit]]


def fts_query(text: str, limit: int = 18) -> str:
    """A safe FTS5 OR-query. Never pass user text to FTS5 unquoted."""
    terms = keywords(text, limit)
    return " OR ".join(f'"{t}"' for t in terms)


class Corpus:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or config.CORPUS_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Corpus":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -----------------------------------------------------------
    def add(self, doc: dict) -> None:
        doc_id = doc["doc_id"]
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM docs_fts WHERE doc_id = ?", (doc_id,))
        self.conn.execute(
            """INSERT INTO documents (doc_id, source_type, ref, project, status,
                 resolution, created, updated, reporter, assignee, labels, components,
                 title, body, url, links)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, doc["source_type"], doc["ref"], doc.get("project"),
             doc.get("status"), doc.get("resolution"), doc.get("created"),
             doc.get("updated"), doc.get("reporter"), doc.get("assignee"),
             json.dumps(doc.get("labels") or []), json.dumps(doc.get("components") or []),
             doc.get("title") or "", doc.get("body") or "", doc.get("url"),
             json.dumps(doc.get("links") or [])),
        )
        self.conn.execute(
            "INSERT INTO docs_fts (title, body, doc_id) VALUES (?,?,?)",
            (doc.get("title") or "", doc.get("body") or "", doc_id),
        )

    def add_many(self, docs: Iterable[dict]) -> int:
        n = 0
        self.conn.execute("BEGIN")
        try:
            for doc in docs:
                self.add(doc)
                n += 1
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return n

    def clear(self) -> None:
        self.conn.executescript("DELETE FROM documents; DELETE FROM docs_fts;")

    # -- reading -----------------------------------------------------------
    def get(self, doc_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id = ?",
                                (doc_id,)).fetchone()
        return _row_to_doc(row) if row else None

    def by_ref(self, ref: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM documents WHERE ref = ? LIMIT 1",
                                (ref,)).fetchone()
        return _row_to_doc(row) if row else None

    def search(self, text: str, *, limit: int = 10, source_types: tuple[str, ...] | None = None,
               project: str | None = None, statuses: tuple[str, ...] | None = None,
               exclude_refs: tuple[str, ...] = ()) -> list[dict]:
        query = fts_query(text)
        if not query:
            return []
        sql = ["""SELECT d.*, bm25(docs_fts) AS bm25
                  FROM docs_fts JOIN documents d ON d.doc_id = docs_fts.doc_id
                  WHERE docs_fts MATCH ?"""]
        params: list = [query]
        if source_types:
            sql.append(f"AND d.source_type IN ({','.join('?' * len(source_types))})")
            params += list(source_types)
        if project:
            sql.append("AND d.project = ?")
            params.append(project)
        if statuses:
            sql.append(f"AND d.status IN ({','.join('?' * len(statuses))})")
            params += list(statuses)
        if exclude_refs:
            sql.append(f"AND d.ref NOT IN ({','.join('?' * len(exclude_refs))})")
            params += list(exclude_refs)
        sql.append("ORDER BY bm25 LIMIT ?")
        params.append(max(limit * 4, 40))
        rows = self.conn.execute(" ".join(sql), params).fetchall()

        query_terms = set(keywords(text, 18))
        # Absolute similarity — the share of the query's INFORMATION a document
        # carries, weighting each term by how rare it is in the corpus. Never
        # normalise against the best hit in the result set: with one weak hit
        # that would score it 1.0.
        weights = {t: self.idf(t) for t in query_terms}
        total_weight = sum(weights.values()) or 1.0
        scored = []
        for row in rows:
            doc = _row_to_doc(row)
            raw = -float(row["bm25"])  # bm25(): lower is better
            doc["score"] = round(raw * SOURCE_WEIGHT.get(doc["source_type"], 1.0), 4)
            doc_terms = set(keywords(f"{doc['title']} {doc['body']}", 400))
            shared = query_terms & doc_terms
            shared_weight = sum(weights[t] for t in shared)
            # Coverage runs both ways. One direction alone misses the duplicate
            # that matters most: a short existing ticket whose whole content sits
            # inside a longer new one ("Create New Location - Central Si Racha"
            # inside "Add two new locations ... Si Racha and Happitat").
            cov_query = shared_weight / total_weight if query_terms else 0.0
            # The other direction measures the document's SUBJECT, which is its
            # title. Measuring it against title + every comment would let a long
            # discussion bury the fact that the request is the same one.
            subject = set(keywords(doc["title"], 60)) or doc_terms
            subject_shared = query_terms & subject
            subject_weight = sum(self.idf(t) for t in subject) or 1.0
            cov_doc = sum(self.idf(t) for t in subject_shared) / subject_weight
            # A two-word document would otherwise match everything it mentions.
            # One shared word is not a match. "Cancel" alone tied a purchase-order
            # request to a payment-gateway ticket at 0.64.
            if len(subject_shared) < 2 and not any(self.is_rare_term(t)
                                                   for t in subject_shared):
                cov_doc = 0.0
            if len(shared) < 2 and not any(self.is_rare_term(t) for t in shared):
                cov_query = 0.0
            # Damp documents far longer than a ticket: they contain a bit of
            # everything, and coverage of the query alone would float them to
            # the top of every search.
            length_penalty = min(1.0, (REFERENCE_TERMS / max(len(doc_terms), 1))
                                 ** LENGTH_DAMPING)
            doc["similarity"] = round(max(cov_query * length_penalty, cov_doc), 3)
            doc["shared_terms"] = sorted(shared, key=lambda t: -weights[t])[:6]
            scored.append(doc)
        scored.sort(key=lambda d: (-d["similarity"], -d["score"]))
        return scored[:limit]

    def stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT source_type, COUNT(*) c FROM documents GROUP BY source_type")
        out = {r["source_type"]: r["c"] for r in rows}
        out["total"] = sum(out.values())
        return out


    # -- term statistics ---------------------------------------------------
    def rebuild_term_stats(self) -> int:
        """Recount document frequencies. Call after adding documents."""
        counts: dict[str, int] = {}
        rows = self.conn.execute("SELECT title, body FROM documents")
        total = 0
        for row in rows:
            total += 1
            for term in set(keywords(f"{row['title']} {row['body']}", 400)):
                counts[term] = counts.get(term, 0) + 1
        self.conn.execute("BEGIN")
        self.conn.execute("DELETE FROM term_df")
        self.conn.executemany("INSERT INTO term_df (term, df) VALUES (?, ?)",
                              counts.items())
        self.conn.execute("COMMIT")
        self._df_cache = None
        LOG.info("corpus.term_stats", documents=total, terms=len(counts))
        return total

    def _df(self) -> tuple[dict[str, int], int]:
        cached = getattr(self, "_df_cache", None)
        if cached is None:
            rows = self.conn.execute("SELECT term, df FROM term_df")
            df = {r["term"]: r["df"] for r in rows}
            total = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
            if not df and total:
                # Documents were added without rebuilding the statistics. Left
                # alone, every term would weigh the same and similarity would
                # quietly fall back to counting words.
                LOG.warn("corpus.term_stats_missing", documents=total)
                self.rebuild_term_stats()
                rows = self.conn.execute("SELECT term, df FROM term_df")
                df = {r["term"]: r["df"] for r in rows}
            cached = (df, max(total, 1))
            self._df_cache = cached
        return cached

    def idf(self, term: str) -> float:
        df, total = self._df()
        return math.log(1 + total / (1 + df.get(term, 0)))

    def is_rare_term(self, term: str) -> bool:
        """A term specific enough to identify a thing — a shop, a system, an id.

        Defined against the corpus rather than as a fixed score, so it means the
        same on 900 documents as on 90.
        """
        df, total = self._df()
        return df.get(term, 0) <= max(2, int(total * 0.005))


def _row_to_doc(row: sqlite3.Row) -> dict:
    doc = dict(row)
    doc["labels"] = json.loads(doc.get("labels") or "[]")
    doc["components"] = json.loads(doc.get("components") or "[]")
    doc["links"] = json.loads(doc.get("links") or "[]")
    return doc


# -- document builders ------------------------------------------------------
def doc_from_jira(issue: dict) -> dict:
    f = issue.get("fields", {}) or {}
    comments = [c.get("body") or "" for c in
                (f.get("comment", {}) or {}).get("comments", [])] or issue.get("_comments", [])
    body_parts = [f.get("description") or ""]
    body_parts += [c if isinstance(c, str) else (c.get("body") or "") for c in comments]
    return {
        "doc_id": f"jira:{issue['key']}",
        "source_type": "jira",
        "ref": issue["key"],
        "project": issue["key"].split("-")[0],
        "status": ((f.get("status") or {}).get("name")),
        "resolution": ((f.get("resolution") or {}) or {}).get("name"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "reporter": ((f.get("reporter") or {}) or {}).get("displayName"),
        "assignee": ((f.get("assignee") or {}) or {}).get("displayName"),
        "labels": f.get("labels") or [],
        "components": [c.get("name") for c in (f.get("components") or [])],
        "title": f.get("summary") or "",
        "body": "\n\n".join(p for p in body_parts if p),
        "url": config.ticket_url(issue["key"]),
        "links": [
            {
                "key": (l.get("inwardIssue") or l.get("outwardIssue") or {}).get("key"),
                "type": (l.get("type") or {}).get("name"),
                "status": (((l.get("inwardIssue") or l.get("outwardIssue") or {})
                            .get("fields") or {}).get("status") or {}).get("name"),
            }
            for l in (f.get("issuelinks") or [])
            if (l.get("inwardIssue") or l.get("outwardIssue"))
        ],
    }


def doc_from_markdown(path: Path, source_type: str = "sop") -> dict:
    text = path.read_text(encoding="utf-8")
    first = next((ln for ln in text.splitlines() if ln.strip()), path.stem)
    return {
        "doc_id": f"{source_type}:{path.stem}",
        "source_type": source_type,
        "ref": path.stem,
        "title": first.lstrip("# ").strip(),
        "body": text,
        "url": str(path),
    }


def build(clear: bool = True) -> dict:
    """(Re)build the index from corpus/raw/*.jsonl plus knowledge/sops/*.md."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    docs: list[dict] = []
    for path in sorted(RAW_DIR.glob("*.jsonl")):
        # str.splitlines() breaks on far more than "\n" — it also treats
        # U+2028/U+2029 and other Unicode line separators as line breaks. A
        # ticket body pasted from Word or Confluence can carry one of those,
        # which fractured its JSON record mid-string and stopped the whole
        # rebuild. JSONL is newline-delimited by definition, so splitting on
        # a literal "\n" is the correct read, not merely a safer one.
        for line in path.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            docs.append(doc_from_jira(record) if "fields" in record else record)
    sop_dir = config.KNOWLEDGE_DIR / "sops"
    if sop_dir.exists():
        docs += [doc_from_markdown(p) for p in sorted(sop_dir.glob("*.md"))]

    with Corpus() as corpus:
        if clear:
            corpus.clear()
        n = corpus.add_many(docs)
        corpus.rebuild_term_stats()
        stats = corpus.stats()
    LOG.info("corpus.built", documents=n, **stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpus.index")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_b = sub.add_parser("build")
    p_b.add_argument("--keep", action="store_true", help="do not clear existing docs")
    p_s = sub.add_parser("search")
    p_s.add_argument("text")
    p_s.add_argument("--limit", type=int, default=10)
    p_s.add_argument("--type", dest="types", action="append", default=[])
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        print(json.dumps(build(clear=not args.keep), indent=2))
    elif args.cmd == "stats":
        with Corpus() as c:
            print(json.dumps(c.stats(), indent=2))
    else:
        with Corpus() as c:
            for doc in c.search(args.text, limit=args.limit,
                                source_types=tuple(args.types) or None):
                print(f"{doc['similarity']:.3f}  {doc['source_type']:<10} "
                      f"{doc['ref']:<14} {doc['title'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
