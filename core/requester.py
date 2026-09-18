"""Requester resolution — CLAUDE.md rules 1-4, in order.

Rule 6 of the invariants: never infer an email from a name alone. Rule 3 here
requires name AND timestamp AND text similarity to agree before it will offer a
medium-confidence match, and it always shows the matched row so the human can
eyeball it. Anything weaker is `unknown` plus a flag.

    python -m core.requester PESD1-11274 --sheet corpus/raw/intake_sheet.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from core import config, log
from corpus.index import keywords

LOG = log.get("requester")

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")

#: rule 3 thresholds — all three must pass.
NAME_RATIO = 0.62
TEXT_SIMILARITY = 0.30
WINDOW_BEFORE = dt.timedelta(hours=48)
WINDOW_AFTER = dt.timedelta(hours=4)


@dataclass
class RequesterMatch:
    email: str | None
    source: str          # sheet | jira_field | unknown
    confidence: str      # high | medium | unknown
    matched_row: dict
    flags: list[str]

    def to_dict(self) -> dict:
        return {"email": self.email, "source": self.source,
                "confidence": self.confidence, "matched_row": self.matched_row}


UNKNOWN = RequesterMatch(None, "unknown", "unknown", {}, ["requester_unknown"])


# -- sheet handling ---------------------------------------------------------
def load_sheet(path: Path | str | None = None) -> list[dict]:
    path = Path(path or config.env("REQUESTER_SHEET_CSV", "corpus/raw/intake_sheet.csv"))
    if not path.is_absolute():
        path = config.ROOT / path
    if not path.exists():
        LOG.warn("requester.sheet_missing", path=str(path))
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _column(rows: list[dict], *hints: str, exclude: tuple[str, ...] = ()) -> str | None:
    """Pick a column by header. Exact header match wins over a substring match,
    so a `request` hint does not silently bind to `requester_name`."""
    if not rows:
        return None
    headers = [c for c in rows[0] if c not in exclude]
    norm = {c: c.lower().strip().replace(" ", "_") for c in headers}
    for hint in hints:
        for col in headers:
            if norm[col] == hint:
                return col
    for hint in hints:
        for col in headers:
            if hint in norm[col]:
                return col
    return None


def _email_in_row(row: dict) -> str | None:
    for value in row.values():
        m = EMAIL_RE.search(str(value or ""))
        if m:
            return m.group(0)
    return None


def _parse_ts(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value[:len(fmt) + 4], fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _jira_created(raw: str) -> dt.datetime | None:
    if not raw:
        return None
    try:  # 2026-09-16T07:55:00.000+0700
        return dt.datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return _parse_ts(raw)


def name_ratio(a: str, b: str) -> float:
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if tokens_a & tokens_b:
        return max(0.8, difflib.SequenceMatcher(None, a, b).ratio())
    return difflib.SequenceMatcher(None, a, b).ratio()


def text_similarity(a: str, b: str) -> float:
    ka, kb = set(keywords(a, 40)), set(keywords(b, 40))
    if not ka or not kb:
        return 0.0
    overlap = len(ka & kb)
    jaccard = overlap / len(ka | kb)
    # Sheet blurbs are much shorter than a ticket body, so plain Jaccard is
    # unfairly harsh: also measure how much of the shorter text is contained in
    # the longer one (only when the shorter side has enough words to mean it).
    smaller = min(len(ka), len(kb))
    containment = overlap / smaller if smaller >= 3 else 0.0
    seq = difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()
    return max(jaccard, containment, seq * 0.8)


# -- the rules ---------------------------------------------------------------
def resolve(issue: dict, sheet: list[dict] | None = None) -> RequesterMatch:
    key = issue["key"]
    fields = issue.get("fields", {}) or {}
    sheet = load_sheet() if sheet is None else sheet

    # Rule 1 — the Jira custom field. Checked first; it may make the sheet moot.
    field_id = config.boards()["intake"].get("requester_email_field")
    raw = fields.get(field_id) if field_id else None
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("emailAddress")
    if raw and (m := EMAIL_RE.search(str(raw))):
        LOG.info("requester.jira_field", ticket=key)
        return RequesterMatch(m.group(0), "jira_field", "high",
                              {"field": field_id}, [])

    if not sheet:
        return RequesterMatch(None, "unknown", "unknown", {}, ["requester_unknown"])

    # Rule 2 — exact join on a column carrying the ticket key.
    for row in sheet:
        for col, value in row.items():
            if not value:
                continue
            if key in KEY_RE.findall(str(value).upper()):
                email = _email_in_row(row)
                if email:
                    LOG.info("requester.sheet_join", ticket=key, column=col)
                    return RequesterMatch(email, "sheet", "high", row, [])
                LOG.warn("requester.sheet_join_no_email", ticket=key)
                return RequesterMatch(None, "unknown", "unknown", row,
                                      ["requester_unknown",
                                       "sheet row matched the key but has no email"])

    # Rule 3 — name AND timestamp AND text must all agree.
    reporter = ((fields.get("reporter") or {}) or {}).get("displayName") or ""
    created = _jira_created(fields.get("created") or "")
    body = f"{fields.get('summary') or ''} {fields.get('description') or ''}"
    email_col = _column(sheet, "requester_email", "email", "e-mail")
    name_col = _column(sheet, "requester_name", "name", "submitted_by", "from",
                       exclude=(email_col,) if email_col else ())
    ts_col = _column(sheet, "timestamp", "submitted", "date", "created",
                     exclude=tuple(c for c in (email_col, name_col) if c))
    text_col = _column(sheet, "request", "description", "message", "issue", "detail",
                       exclude=tuple(c for c in (email_col, name_col, ts_col) if c))

    best: tuple[float, dict, dict] | None = None
    for row in sheet:
        email = _email_in_row(row)
        if not email:
            continue
        n = name_ratio(reporter, row.get(name_col, "") if name_col else "")
        submitted = _parse_ts(row.get(ts_col, "")) if ts_col else None
        in_window = bool(
            created and submitted
            and (created - WINDOW_BEFORE) <= submitted <= (created + WINDOW_AFTER)
        )
        t = text_similarity(body, row.get(text_col, "") if text_col else "")
        if n >= NAME_RATIO and in_window and t >= TEXT_SIMILARITY:
            score = n + t
            evidence = {"name_ratio": round(n, 3), "text_similarity": round(t, 3),
                        "submitted": str(submitted), "ticket_created": str(created)}
            if best is None or score > best[0]:
                best = (score, row, evidence)

    if best:
        _, row, evidence = best
        matched = dict(row)
        matched["_match"] = evidence
        LOG.info("requester.fuzzy_match", ticket=key, **evidence)
        return RequesterMatch(_email_in_row(row), "sheet", "medium", matched, [])

    # Rule 4 — never guess.
    LOG.info("requester.unknown", ticket=key)
    return RequesterMatch(None, "unknown", "unknown", {}, ["requester_unknown"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.requester")
    ap.add_argument("ticket")
    ap.add_argument("--sheet")
    ap.add_argument("--from-file", help="read the issue from a fixtures jsonl instead of Jira")
    args = ap.parse_args(argv)

    if args.from_file:
        issue = next(json.loads(l) for l in Path(args.from_file).read_text().splitlines()
                     if l.strip() and json.loads(l)["key"] == args.ticket)
    else:
        from core.jira_client import JiraReadClient

        issue = JiraReadClient().issue(args.ticket)
    match = resolve(issue, load_sheet(args.sheet) if args.sheet else None)
    print(json.dumps({**match.to_dict(), "flags": match.flags}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
