"""The two gates a worker runs before it is allowed to think about a ticket.

Gate 2 (sanity): can the requirement be stated in one sentence? If not the
ticket is escalated untouched.
Gate 3 (never-touch): refunds, payments, pricing, customer PII, stock
adjustments, account access — escalated with NO comment and NO clone, whatever
the confidence (invariant 3).
"""
from __future__ import annotations

import argparse
import json
import re

from core import config, log
from corpus.index import keywords

LOG = log.get("gates")

#: words that carry no information about what is actually being asked
VAGUE = {"broken", "again", "same", "before", "asap", "urgent", "help", "issue",
         "problem", "fix", "please", "pls", "working", "error", "wrong", "bad",
         "thing", "stuff", "something", "anything", "everything", "usual"}

#: at least one of these has to appear, or we cannot say what the ticket is about
DOMAIN_TERMS = {
    "stock", "inventory", "sku", "product", "catalog", "catalogue", "image", "price",
    "order", "shipment", "tracking", "delivery", "return", "refund", "payment",
    "checkout", "cart", "voucher", "coupon", "promo", "customer", "account", "login",
    "report", "export", "import", "sync", "warehouse", "store", "storefront", "page",
    "pdp", "email", "campaign", "app", "site", "api", "job", "batch", "upload",
}

MIN_CHARS = 40


def _text_of(issue: dict) -> str:
    f = issue.get("fields", {}) or {}
    labels = " ".join(f.get("labels") or [])
    components = " ".join(c.get("name", "") for c in (f.get("components") or []))
    return f"{f.get('summary') or ''}\n{f.get('description') or ''}\n{labels} {components}"


# -- gate 2: sanity ---------------------------------------------------------
def restate(issue: dict) -> tuple[str | None, str]:
    """Return (one-sentence requirement, reason). None means: escalate."""
    f = issue.get("fields", {}) or {}
    summary = (f.get("summary") or "").strip()
    description = (f.get("description") or "").strip()
    blob = f"{summary} {description}".strip()

    if len(blob) < MIN_CHARS:
        return None, f"too little text to restate ({len(blob)} chars)"

    words = keywords(blob, 40)
    if not words:
        return None, "no content words"
    domain_hits = [w for w in words if w in DOMAIN_TERMS]
    component_vocab = {kw for words_ in
                       (config.repos().get("component_keywords") or {}).values()
                       for kw in words_}
    domain_hits += [w for w in words if w in component_vocab and w not in domain_hits]
    if not domain_hits:
        return None, "no recognisable subject — cannot say what the request is about"
    vague_ratio = sum(1 for w in words if w in VAGUE) / len(words)
    if vague_ratio > 0.5:
        return None, f"mostly filler words ({vague_ratio:.0%})"

    first = re.split(r"(?<=[.!?])\s+", description)[0].strip() if description else ""
    sentence = summary if len(summary) >= 15 else (first or summary)
    sentence = " ".join(sentence.split())[:380]
    asking = bool(re.search(r"\b(how|where|what|which|can you|could you|who)\b",
                            blob.lower()[:400])) and "?" in blob
    prefix = "Requester asks: " if asking else "Requester reports: "
    return prefix + sentence, f"subject terms: {', '.join(domain_hits[:5])}"


# -- gate 3: never-touch ----------------------------------------------------
def never_touch(issue: dict) -> list[dict]:
    """Every never-touch category this ticket matches, with the matched term."""
    blob = _text_of(issue).lower()
    hits: list[dict] = []
    cfg = config.never_touch()
    for category, spec in (cfg.get("categories") or {}).items():
        for term in (spec.get("keywords") or []):
            if term.lower() in blob:
                hits.append({"category": category, "match": term, "kind": "keyword"})
                break
    for pattern in (cfg.get("regexes") or []):
        m = re.search(pattern, blob, re.I)
        if m:
            hits.append({"category": "regex", "match": m.group(0)[:40],
                         "kind": "regex", "pattern": pattern})
    if hits:
        LOG.info("gate.never_touch", ticket=issue.get("key"),
                 categories=[h["category"] for h in hits])
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.gates")
    ap.add_argument("file", help="jsonl of Jira issues to run the gates over")
    args = ap.parse_args(argv)
    for line in open(args.file, encoding="utf-8"):
        if not line.strip():
            continue
        issue = json.loads(line)
        sentence, reason = restate(issue)
        nt = never_touch(issue)
        print(json.dumps({"ticket": issue["key"],
                          "sanity": bool(sentence), "reason": reason,
                          "restated": sentence,
                          "never_touch": [h["category"] for h in nt]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
