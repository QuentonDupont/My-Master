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

#: an actionable signal — a request verb, a question, or a reported problem.
#: Real PESD1 tickets carry the whole request in the summary with an empty
#: description, so this is what "can I state the requirement?" comes down to.
REQUEST_VERBS = {
    "add", "remove", "update", "change", "cancel", "create", "delete", "revert",
    "roll", "rollback", "correct", "adjust", "enable", "disable", "move", "merge",
    "split", "upload", "download", "export", "import", "sync", "resync", "link",
    "unlink", "assign", "reassign", "activate", "deactivate", "restore", "reopen",
    "close", "approve", "reject", "check", "verify", "investigate", "configure",
    "set", "reset", "increase", "decrease", "extend", "renew", "generate", "send",
}
PROBLEM_SIGNALS = (
    "not working", "doesn't work", "does not work", "not showing", "not updating",
    "incorrect", "wrong", "missing", "failed", "failing", "error", "stuck", "hang",
    "duplicate", "cannot", "can't", "unable", "rejected", "invalid", "stale",
    "timeout", "crash", "mismatch", "did not", "didn't", "no longer",
    "not received", "not arrived", "has not", "hasn't", "haven't", "never received",
)
QUESTION_WORDS = ("how", "where", "what", "which", "who", "when", "why")

#: a polite ask counts as a request whatever verb follows it — a verb whitelist
#: alone misses "Please process the refund again".
REQUEST_PATTERNS = re.compile(
    r"\b(please|pls|kindly|could you|can you|would you|we need|i need|needs? to"
    r"|request(?:ing)? (?:to|for|that)|help (?:me |us )?(?:to )?\w+)\b", re.I)

MIN_CHARS = 20
URL_RE = re.compile(r'https?://\S+')


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
    # A bare link is not a requirement — the detail is inside the attachment.
    blob = URL_RE.sub(" ", f"{summary} {description}").strip()

    if len(blob) < MIN_CHARS:
        return None, f"too little text to restate ({len(blob)} chars)"

    words = keywords(blob, 40)
    if len(words) < 3:
        return None, f"only {len(words)} content words"

    lower = blob.lower()
    verbs = [w for w in words if w in REQUEST_VERBS]
    problems = [p for p in PROBLEM_SIGNALS if p in lower]
    asking = any(re.search(rf"\b{w}\b", lower) for w in QUESTION_WORDS) and "?" in blob
    polite = bool(REQUEST_PATTERNS.search(lower))
    if not (verbs or problems or asking or polite):
        return None, ("no request and no reported problem — cannot say what is "
                      "being asked for")

    vague_ratio = sum(1 for w in words if w in VAGUE) / len(words)
    if vague_ratio > 0.5:
        return None, f"mostly filler words ({vague_ratio:.0%})"

    component_vocab = {kw for words_ in
                       (config.repos().get("component_keywords") or {}).values()
                       for kw in words_}
    subject = [w for w in words if w in component_vocab][:4]

    first = re.split(r"(?<=[.!?])\s+", description)[0].strip() if description else ""
    sentence = summary if len(summary) >= 15 else (first or summary)
    sentence = " ".join(URL_RE.sub("", sentence).split())[:380]
    prefix = "Requester asks: " if asking else "Requester reports: "
    why = (f"subject: {', '.join(subject)}" if subject
           else (f"request verb: {verbs[0]}" if verbs
                 else f"problem: {problems[0]}" if problems
                 else "explicit request" if polite else "question"))
    return prefix + sentence, why


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
