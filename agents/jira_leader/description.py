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
    names = [m.strip() for m in QUOTED_RE.findall(clean)]
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
    """The full clone description."""
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

    out: list[str] = []

    # -- overview ----------------------------------------------------------
    out += ["h2. Overview", "",
            f"{analysis.requirement_restated}", "",
            f"Raised on {key} and cloned here for the {component} area.", ""]

    # -- current behaviour -------------------------------------------------
    current = _pick(sentences, PROBLEM_MARKERS)
    out += ["h2. Current result", ""]
    out += ([f"* {s}" for s in current] if current else
            ["_The ticket does not describe the current behaviour. Confirm with the "
             "requester before starting._"])
    out.append("")

    # -- expected outcome --------------------------------------------------
    expected = _pick(sentences, EXPECTATION_MARKERS)
    out += ["h2. Expected outcome", ""]
    out += ([f"* {s}" for s in expected] if expected else
            [f"* {analysis.requirement_restated}"])
    out.append("")

    # -- details -----------------------------------------------------------
    reporter = ((fields.get("reporter") or {}) or {}).get("displayName") or "unknown"
    raised = (fields.get("created") or "")[:10]
    from agents.jira_leader.analysis import clone_priority

    priority, priority_why, _ = clone_priority(issue)
    rows = [["Source ticket", key],
            ["Reported by", reporter],
            ["Raised", raised],
            ["Priority", f"{priority} — {priority_why}"]]
    if requester is not None and getattr(requester, "email", None):
        rows.append(["Requester", f"{requester.email} ({requester.confidence} confidence)"])
    if ents["names"]:
        rows.append(["Shops / entities named", ", ".join(ents["names"])])
    if ents["references"]:
        rows.append(["References", ", ".join(ents["references"])])
    if fields.get("labels"):
        rows.append(["Labels on the request", ", ".join(fields["labels"])])
    out += ["h2. Request details", ""] + _table(rows, ["Field", "Value"]) + [""]

    if ents["links"]:
        out += ["The requester attached detail outside Jira — read this before "
                "estimating:", ""]
        out += [f"* {u}" for u in ents["links"]] + [""]

    # -- verbatim ----------------------------------------------------------
    verbatim = "\n\n".join(p for p in [summary, body] if p)
    out += ["h2. Original request (verbatim)", "", "{quote}", verbatim, "{quote}", ""]
    if comments:
        out += ["Comments on the request:", "", "{quote}",
                "\n\n".join(comments[:3])[:1200], "{quote}", ""]

    # -- history -----------------------------------------------------------
    history = [d for d in (retrieval.similar_resolved or [])
               if d.get("similarity", 0) >= HISTORY_MIN_SIMILARITY][:MAX_HISTORY]
    out += ["h2. Related history", ""]
    if history:
        rows = []
        for doc in history:
            outcome = doc.get("resolution") or doc.get("status") or "—"
            who = doc.get("assignee") or "unassigned"
            note = resolution_note(doc) or "no resolution note on the ticket"
            rows.append([doc["ref"], doc["title"][:70], f"{outcome} ({who})", note])
        out += _table(rows, ["Ticket", "What it was", "Outcome", "How it was resolved"])
        out.append("")
        if ents["links"] and not body:
            out += ["_These were matched on the request's title alone, because its "
                    "detail is in the attachment above. Read the attachment before "
                    "trusting the comparison._", ""]
    else:
        weak = (retrieval.similar_resolved or [])[:2]
        out += [f"_Nothing comparable enough to be worth reading in the last 6 months "
                f"of {config.intake_project()} and {config.dev_project()}. Treat this "
                f"as new ground._", ""]
        if weak:
            out += ["The nearest tickets were "
                    + ", ".join(f"{d['ref']} ({d.get('similarity', 0):.0%} overlap)"
                                for d in weak)
                    + " — checked and judged unrelated.", ""]

    overlap = (retrieval.duplicates or []) + (retrieval.related or [])
    if overlap:
        out += ["h3. Possibly the same request", ""]
        rows = []
        for doc in overlap:
            clones = ", ".join(f"{c['key']} ({c['status']})"
                               for c in doc.get("open_clones", [])) or "no dev ticket"
            rows.append([doc["ref"], doc["title"][:70], clones])
        out += _table(rows, ["Open ticket", "What it asks for", "Dev work"])
        out += ["", "*Check these before starting — the work may already be under way.*",
                ""]

    # -- where to start ----------------------------------------------------
    out += ["h2. Where to start", ""]
    started = False
    if retrieval.sops:
        sop = retrieval.sops[0]
        out += [f"* Follow the SOP _{sop['title']}_ — it covers this symptom.", ""]
        started = True
    if history:
        # The nearest ticket by wording is not always the one that says what was
        # done — pick the precedent whose resolution note actually explains
        # something, preferring closer matches when they are comparable.
        def informativeness(note: str) -> int:
            low = note.lower()
            return (sum(m in low for m in DONE_MARKERS)
                    - 2 * sum(m in low for m in PENDING_MARKERS))

        scored = [(doc, resolution_note(doc, 400)) for doc in history]
        usable = [(doc, note) for doc, note in scored if len(note) > 60]
        if usable:
            best, note = max(usable, key=lambda pair: (informativeness(pair[1]),
                                                       pair[0].get("similarity", 0)))
            out += [f"* {best['ref']} is the closest precedent that records what was "
                    f"done: {note}", ""]
            started = True
    if not started:
        out += ["* No precedent and no SOP. Scope this one before estimating.", ""]

    # -- open questions ----------------------------------------------------
    questions = []
    if requester is not None and not getattr(requester, "email", None):
        questions.append("Who raised this? The requester could not be identified "
                         "automatically — ask before replying directly.")
    if ents["links"]:
        questions.append("The detail is in an attachment the triage system cannot "
                         "read; the sections above come from the ticket text only.")
    if not current:
        questions.append("The current behaviour is not described.")
    if overlap:
        questions.append("Possible duplicate — see the table above.")
    if questions:
        out += ["h2. Open questions", ""] + [f"* {q}" for q in questions] + [""]

    out += ["----", f"_Drafted by the triage system from {key} and the tickets above, "
            f"and approved by a human before it was created._"]
    return "\n".join(out).strip()
