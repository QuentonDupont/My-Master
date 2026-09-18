"""Write the clone description a developer can actually work from.

Jira renders wiki markup, so the sections below come out as real headings and
tables. The structure is fixed by the board owner:

    Overview → Current behaviour → Expected outcome → Request details →
    Original request (verbatim) → Related history (read, not just cited) →
    Where to start → Open questions

Nothing here invents facts. Every section either comes from the PESD1 ticket or
from a ticket the Historian retrieved, and says so when it has nothing to say.
"""
from __future__ import annotations

import re

from core import config

URL_RE = re.compile(r"https?://\S+")
#: Jira renders these as a name; as text they are noise in a resolution note.
MENTION_RE = re.compile(r"\[~accountid:[^\]]+\]")
#: Jira embeds attachments as !name.png|width=780! — a screenshot is not a
#: resolution note, and pasting the macro into another ticket renders nothing.
IMAGE_RE = re.compile(r"!\S+?\.(?:png|jpe?g|gif|bmp|webp)(?:\|[^!]*)?!", re.I)
#: order ids, invoice numbers, IR numbers — 5+ digits, optionally prefixed
REFERENCE_RE = re.compile(r"\b(?:[A-Z]{2,4}-)?\d{5,}\b")
#: "TH Central Si Racha", "Central Ubon" — quoted or title-cased runs
QUOTED_RE = re.compile(r"[\"“']([^\"”']{3,60})[\"”']")
TITLECASE_RE = re.compile(r"\b(?:[A-Z][a-z]{2,}|TH|SG|MY|PH|ID|WH|NS|PO|IR)"
                          r"(?:\s+(?:[A-Z][a-z]{2,}|[A-Z]{2,4})){1,4}\b")

PROBLEM_MARKERS = (
    "not ", "n't", "cannot", "unable", "incorrect", "wrong", "missing", "failed",
    "error", "stuck", "duplicate", "mismatch", "did not", "no longer", "still ",
)
EXPECTATION_MARKERS = (
    "should", "need", "needs", "expect", "please", "would like", "want", "so that",
    "must", "require", "so we can", "to be correct",
)
#: auto-replies and pleasantries that are not a resolution
NOISE_COMMENT = (
    "we received your ticket", "we will check", "thank you", "thanks", "noted",
    "please check", "any update", "following up", "kindly check",
)

MAX_HISTORY = 4
#: words that mark a note as an account of work completed...
DONE_MARKERS = ("created", "removed", "deleted", "fixed", "resolved", "added",
                "reverted", "updated", "corrected", "released", "deployed",
                "configured", "re-synced", "resynced", "cancelled", "closed",
                "are fully", "has been", "have been", "was done")
#: ...and words that mark it as a status update with nothing to learn from
PENDING_MARKERS = ("waiting", "will check", "will update", "pending", "on hold",
                   "any update", "following up", "we will", "please advise")
#: A weak match in the history table is worse than an empty one: it sends the
#: developer to read a voucher ticket about a purchase-order problem. This is
#: deliberately stricter than the floor for citing evidence on a proposal.
HISTORY_MIN_SIMILARITY = 0.35
#: Calling a page "the procedure" sends a developer off to read it, so the claim
#: has to be earned. A score alone cannot separate "New Retail Location V1" (0.38,
#: exactly right) from "SG Store Visit - Engineers 28 Jun 2019" (0.43, noise
#: matched deep in its body). A page whose TITLE shares subject words with the
#: request can: a procedure is about the thing its title names.
PROCEDURE_MIN_SIMILARITY = 0.30
#: title-case runs starting with these are the request, not a place or a thing
_NOT_ENTITY_LEAD = {
    "cancel", "add", "update", "remove", "create", "delete", "please", "revert",
    "change", "correct", "check", "request", "kindly", "need", "hello", "issue",
    "critical", "new", "the", "and", "for", "from", "with",
}


def _sentences(text: str) -> list[str]:
    clean = URL_RE.sub(" ", text or "")
    parts = re.split(r"(?<=[.!?])\s+|\n+", clean)
    return [" ".join(p.split()) for p in parts if len(p.strip()) > 12]


def _pick(sentences: list[str], markers: tuple[str, ...], limit: int = 3) -> list[str]:
    hits = [s for s in sentences if any(m in s.lower() for m in markers)]
    return hits[:limit]


def entities(text: str) -> dict[str, list[str]]:
    """Shops, sites and reference numbers — the details a developer needs to
    reproduce anything."""
    clean = URL_RE.sub(" ", text or "")
    # A quoted phrase is only a name if it looks like one. "invalid code" is the
    # error the requester quoted, not a shop.
    names = [m.strip() for m in QUOTED_RE.findall(clean)
             if any(c.isupper() or c.isdigit() for c in m)]
    for m in TITLECASE_RE.findall(clean):
        m = m.strip()
        words = m.split()
        if len(words) < 2 or m in names:
            continue
        if words[0].lower() in _NOT_ENTITY_LEAD:
            continue  # "Cancel Critical" is the request, not a shop
        names.append(m)
    refs = REFERENCE_RE.findall(clean)
    unique = []
    for name in dict.fromkeys(names):
        if any(name != other and other.startswith(name) for other in names):
            continue  # "TH Central" is the head of "TH Central Si Racha"
        unique.append(name)
    return {
        "names": unique[:8],
        "references": list(dict.fromkeys(refs))[:8],
        "links": list(dict.fromkeys(URL_RE.findall(text or "")))[:6],
    }


def resolution_note(doc: dict, limit: int = 420) -> str:
    """What actually closed a past ticket.

    The corpus stores description and comments joined; the resolution is the last
    substantive comment, not the last paragraph — which is often an
    acknowledgement or an unrelated quoted block.
    """
    chunks = [c.strip() for c in (doc.get("body") or "").split("\n\n") if c.strip()]
    for chunk in reversed(chunks):
        cleaned = " ".join(IMAGE_RE.sub(" ", MENTION_RE.sub("", chunk)).split())
        low = cleaned.lower()
        if len(cleaned) < 25:
            continue
        if any(n in low for n in NOISE_COMMENT) and len(cleaned) < 160:
            continue
        if cleaned.startswith("[") and cleaned.endswith("]"):
            continue  # a bare link, not a resolution
        if not re.search(r"[A-Za-z]{3,}", cleaned):
            continue  # punctuation, ids or leftover markup
        return cleaned[:limit]
    return ""


def _table(rows: list[list[str]], headers: list[str]) -> list[str]:
    out = ["|| " + " || ".join(headers) + " ||"]
    for row in rows:
        cells = [(c or "—").replace("|", "/").replace("\n", " ") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return out


def build(issue: dict, retrieval, analysis, requester=None) -> str:
    """The clone description.

    Written to be scanned, not read: the ask first, the facts as a table, the
    evidence as links. A developer should know what to do in ten seconds and be
    able to go deeper if they need to. Empty sections are omitted rather than
    filled with a sentence saying they are empty.
    """
    fields = issue.get("fields", {}) or {}
    key = issue["key"]
    summary = (fields.get("summary") or "").strip()
    body = (fields.get("description") or "").strip()
    comments = [(c.get("body") or "").strip() for c in
                ((fields.get("comment") or {}).get("comments") or [])]
    comments = [c for c in comments
                if c and not any(n in c.lower() for n in NOISE_COMMENT)]
    full = "\n\n".join([summary, body, *comments]).strip()
    sentences = _sentences(full)
    ents = entities(full)
    component = (retrieval.components or ["unclassified"])[0]
    from agents.jira_leader.analysis import clone_priority

    priority, priority_why, _ = clone_priority(issue)

    out: list[str] = []

    # -- the ask, in one line ---------------------------------------------
    ask = analysis.requirement_restated
    for prefix in ("Requester reports:", "Requester asks:"):
        if ask.startswith(prefix):
            ask = ask[len(prefix):].strip()
    out += [f"h2. Ask", "", ask, ""]

    current = _pick(sentences, PROBLEM_MARKERS, limit=2)
    expected = _pick(sentences, EXPECTATION_MARKERS, limit=2)
    # Only add these when they say something the ask line did not.
    def adds_information(lines: list[str]) -> bool:
        return bool(lines) and not all(line.strip() in ask for line in lines)

    if adds_information(current):
        out += ["*Now:* " + " ".join(current), ""]
    if adds_information(expected):
        out += ["*Wanted:* " + " ".join(expected), ""]

    # -- facts -------------------------------------------------------------
    rows = [["From", f"{key} ({priority}, {(fields.get('created') or '')[:10]})"]]
    if ents["names"]:
        rows.append(["Shops / entities", ", ".join(ents["names"][:5])])
    if ents["references"]:
        rows.append(["References", ", ".join(ents["references"][:5])])
    if requester is not None and getattr(requester, "email", None):
        rows.append(["Requester", requester.email])
    rows.append(["Area", component])
    out += _table(rows, ["", ""]) + [""]

    if ents["links"]:
        out += ["*The detail is in the attachment — read it first:* "
                + " ".join(ents["links"][:3]), ""]

    # -- evidence, as links ------------------------------------------------
    pointers = []
    from corpus.index import keywords as _keywords

    subject_terms = set(_keywords(f"{summary} {body}", 25))
    procedures = [d for d in retrieval.sops
                  if d.get("similarity", 0) >= PROCEDURE_MIN_SIMILARITY
                  and subject_terms & set(_keywords(d.get("title") or "", 20))]
    for sop in procedures[:2]:
        link = sop.get("url") or ""
        title = f"[{sop['title']}|{link}]" if str(link).startswith("http") else sop["title"]
        pointers.append(f"* Procedure: {title}")

    history = [d for d in (retrieval.similar_resolved or [])
               if d.get("similarity", 0) >= HISTORY_MIN_SIMILARITY][:3]
    for doc in history:
        note = resolution_note(doc, 180)
        outcome = doc.get("resolution") or doc.get("status") or "—"
        pointers.append(f"* {doc['ref']} ({outcome})"
                        + (f": {note}" if note else " — no note on the ticket"))
    if pointers:
        out += ["h2. Start here", ""] + pointers + [""]

    overlap = (retrieval.duplicates or []) + (retrieval.related or [])
    if overlap:
        lines = []
        for doc in overlap[:3]:
            clones = ", ".join(c["key"] for c in doc.get("open_clones", []))
            lines.append(f"* {doc['ref']} — {doc['title'][:60]}"
                         + (f" (already in {clones})" if clones else ""))
        out += ["h2. Possibly already covered", ""] + lines + [""]

    # -- the request itself ------------------------------------------------
    verbatim = "\n\n".join(p for p in [summary, body] if p)
    out += ["h2. Original request", "", "{quote}", verbatim, "{quote}", ""]
    if comments:
        out += ["{quote}", "\n\n".join(comments[:2])[:800], "{quote}", ""]

    # -- what nobody knows -------------------------------------------------
    questions = []
    if not current and not body:
        questions.append("The ticket does not say what is happening today.")
    if requester is not None and not getattr(requester, "email", None):
        questions.append("Requester not identified — ask before replying directly.")
    if ents["links"]:
        questions.append("Triage could not read the attachment; the above comes "
                         "from the ticket text only.")
    if not history and not procedures:
        questions.append("No precedent and no written procedure — scope before "
                         "estimating.")
    if questions:
        out += ["h2. Unknowns", ""] + [f"* {q}" for q in questions] + [""]

    return "\n".join(out).strip()
