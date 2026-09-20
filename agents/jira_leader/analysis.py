"""Classification and drafting for a worker.

Two analysts with the same interface:

* `HeuristicAnalyst` — deterministic, stdlib only, no network. The default, and
  what the tests run against.
* `ClaudeAnalyst` — the official `anthropic` SDK with a JSON schema, used when
  ANTHROPIC_API_KEY is set and the package is installed. Any failure falls back
  to the heuristic: a worker must always return a proposal.

Neither writes anything anywhere. They return an `Analysis`; the worker turns it
into a Proposal, and only the human's approval turns that into a Jira write.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field

from core import config, log

LOG = log.get("analysis")

ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["ANSWERABLE", "NEEDS_CODE"]},
        "confidence": {"type": "number"},
        "requirement_restated": {"type": "string"},
        "proposed_comment": {"type": "string"},
        "clone_summary": {"type": "string"},
        "developer_summary": {"type": "string"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["classification", "confidence", "requirement_restated",
                 "proposed_comment", "clone_summary", "developer_summary",
                 "labels", "flags"],
    "additionalProperties": False,
}

FAILURE_SIGNALS = (
    "not updating", "not update", "not working", "doesn't work", "does not work",
    "not showing", "missing", "fails", "failed", "failing", "error", "404", "500",
    "stuck", "hang", "rejected", "invalid", "cannot", "can't", "unable", "broken",
    "wrong", "duplicate", "stale", "timeout", "crash", "oversell", "placeholder",
)
QUESTION_SIGNALS = ("how do i", "how can i", "where do i", "where can i", "where is",
                    "where do", "what is", "which", "can you point", "can someone tell",
                    "is there a way", "how to")

#: an instruction to change something — the dominant shape of a PESD1 ticket.
ACTION_REQUEST = re.compile(
    r"\b(add|remove|delete|cancel|update|change|correct|create|revert|roll ?back"
    r"|adjust|enable|disable|move|merge|split|upload|extend|renew|restore|reopen"
    r"|assign|reassign|configure|set up|activate|deactivate|sync|resync|fix)\b", re.I)

SYSTEM_PROMPT = """You triage support tickets for Pomelo Fashion's PESD1 board.

You produce a PROPOSAL for a human to approve. You never take action, and nothing
you write reaches Jira until a human approves it.

Classify the ticket as exactly one of:
- ANSWERABLE: the requester can be answered now from existing knowledge (SOPs,
  resolved tickets). Write the reply you would post.
- NEEDS_CODE: a developer has to change something. Write the holding reply for
  the requester AND a developer summary for the cloned ticket.

Rules:
- Use only the evidence given. Never invent a ticket key, a name, a root cause or
  a date. If the evidence does not support an answer, say so and lower confidence.
- The comment is read by a colleague at Pomelo. Plain, specific, no marketing tone,
  no apology padding, no markdown headings.
- The clone does not exist yet when the comment is posted, so never quote a PRDT key.
- `developer_summary` states the symptom, the affected area, what the history
  suggests to look at first, and what is unknown.
- `confidence` is your honest probability that a human approves this unedited.
"""


@dataclass
class Analysis:
    classification: str
    confidence: float
    requirement_restated: str
    proposed_comment: str
    clone_summary: str = ""
    developer_summary: str = ""
    labels: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    analyst: str = "heuristic"


#: requesters open with a greeting far more often than not — and often stack
#: several ("Hello, could you please ..."), so strip them one at a time.
_OPENERS = ("hi", "hello", "dear", "hey", "could you", "can you", "would you",
            "please", "pls", "kindly")


def _title(summary: str, limit: int = 110) -> str:
    """A PRDT summary should read as a title, not as the requester's paragraph."""
    text = " ".join((summary or "").strip().split())
    for _ in range(4):
        low = text.lower()
        opener = next((o for o in _OPENERS if low.startswith(o)), None)
        if not opener:
            break
        text = text[len(opener):].lstrip(" ,")
    # Strip a WRAPPING pair of quotes only. A blanket strip of quote characters
    # eats the closing quote of a title like: Add locations "A" and "B"
    while len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    if text:
        text = text[0].upper() + text[1:]
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "\u2026"


#: text that claims urgency the priority field may not reflect
URGENCY_SIGNALS = ("critical", "urgent", "asap", "blocker", "blocking", "oversell",
                   "customers affected", "cannot ship", "stuck", "immediately",
                   "today", "escalat")

PRIORITY_ORDER = ["Low", "Medium", "High", "Critical"]


def clone_priority(issue: dict) -> tuple[str, str, list[str]]:
    """(priority, why, flags) for the clone.

    The requester's urgency is a fact recorded on the PESD1 ticket, so it is
    carried across rather than re-derived. Text that claims more urgency than
    the field shows is flagged for the human, never applied silently.
    """
    dev = config.boards()["development"]
    fields = issue.get("fields", {}) or {}
    source = ((fields.get("priority") or {}) or {}).get("name")
    fallback = dev.get("default_priority") or "Medium"
    flags: list[str] = []

    if not dev.get("carry_priority", True) or not source:
        why = (f"{issue['key']} has no priority set; using the default"
               if not source else "carry_priority is off; using the default")
        return fallback, why, (["priority_defaulted"] if not source else [])

    mapped = (dev.get("priority_map") or {}).get(source, source)
    why = f"carried from {issue['key']} ({source})"

    blob = f"{fields.get('summary') or ''} {fields.get('description') or ''}".lower()
    hits = [w for w in URGENCY_SIGNALS if w in blob]
    if hits and mapped in PRIORITY_ORDER and PRIORITY_ORDER.index(mapped) < \
            PRIORITY_ORDER.index("High"):
        flags.append("urgency_language_above_priority")
        why += f"; the text says {', '.join(hits[:3])} — check the priority is right"
    return mapped, why, flags


def _blob(issue: dict) -> str:
    f = issue.get("fields", {}) or {}
    return f"{f.get('summary') or ''}\n{f.get('description') or ''}"


def _resolution_hint(doc: dict) -> str:
    """The last paragraph of a resolved ticket is usually the resolution note."""
    parts = [p.strip() for p in (doc.get("body") or "").split("\n\n") if p.strip()]
    return parts[-1] if parts else ""


class HeuristicAnalyst:
    """Deterministic classifier. Explains itself; never pretends to know more."""

    name = "heuristic"

    def analyse(self, issue: dict, retrieval, restated: str,
                rules_text: str = "") -> Analysis:
        text = _blob(issue).lower()
        failures = [s for s in FAILURE_SIGNALS if s in text]
        question = (any(q in text for q in QUESTION_SIGNALS) and "?" in text)
        # "Please add X", "Cancel Y", "Update Z" — an instruction to change
        # something, which someone has to do. Not a question to answer.
        action = bool(ACTION_REQUEST.search(text))

        sops = retrieval.sops
        similar = retrieval.similar_resolved
        best = (sops or similar or [None])[0]
        best_sim = float(best.get("similarity", 0)) if best else 0.0

        flags: list[str] = []
        if failures and not question:
            classification = "NEEDS_CODE"
            base = 0.55
        elif question and not failures and not action:
            classification = "ANSWERABLE"
            base = 0.5
        elif question and failures:
            classification = "NEEDS_CODE" if best_sim < 0.7 else "ANSWERABLE"
            base = 0.45
            flags.append("mixed_signals")
        elif action:
            # An explicit ask to change something. It is only ANSWERABLE if an
            # SOP genuinely covers it end to end; otherwise someone has to act.
            covered = bool(sops) and best_sim >= 0.7
            classification = "ANSWERABLE" if covered else "NEEDS_CODE"
            base = 0.55 if covered else 0.5
        else:
            classification = "ANSWERABLE" if best_sim >= 0.6 else "NEEDS_CODE"
            base = 0.4
            flags.append("weak_signals")

        confidence = base
        if best_sim >= 0.7:
            confidence += 0.25
        elif best_sim >= 0.5:
            confidence += 0.12
        if sops:
            confidence += 0.1
        if len(similar) >= 3:
            confidence += 0.05
        confidence = round(min(confidence, 0.95), 2)
        if confidence < 0.5:
            flags.append("low_confidence")
        if not similar and not sops:
            flags.append("no_precedent")

        refs = [d["ref"] for d in (sops + similar)[:3]]
        comment = (self._answer_comment(retrieval, refs)
                   if classification == "ANSWERABLE"
                   else self._holding_comment(refs))

        component = (retrieval.components or ["triage"])[0]
        summary = (issue.get("fields", {}) or {}).get("summary") or ""
        clone_summary = f"[{component}] {_title(summary)}"
        developer_summary = self._developer_summary(retrieval, failures, component)

        labels = list((issue.get("fields", {}) or {}).get("labels") or [])
        if component != "triage" and component not in labels:
            labels.append(component)

        return Analysis(classification=classification, confidence=confidence,
                        requirement_restated=restated, proposed_comment=comment,
                        clone_summary=clone_summary,
                        developer_summary=developer_summary,
                        labels=labels, flags=flags, analyst=self.name)

    # -- drafting ----------------------------------------------------------
    def _answer_comment(self, retrieval, refs: list[str]) -> str:
        source = (retrieval.sops or retrieval.similar_resolved or [None])[0]
        if source and source.get("source_type") in ("sop", "confluence"):
            body = "\n".join(
                ln for ln in (source.get("body") or "").splitlines()
                if ln.strip() and not ln.startswith("#"))[:900]
            answer = f"From the SOP '{source['title']}':\n\n{body}"
        elif source:
            answer = (f"This looks like {source['ref']} ({source['title']}), which was "
                      f"resolved as follows:\n\n{_resolution_hint(source)[:700]}")
        else:
            answer = ("We could not find a documented answer for this yet — a human "
                      "needs to write the reply.")
        cited = f"\n\nReference: {', '.join(refs)}." if refs else ""
        return ("Thanks for raising this.\n\n" + answer + cited +
                "\n\nIf that does not cover it, reply here and we will pick it up.")

    def _holding_comment(self, refs: list[str]) -> str:
        cited = (f" Similar past reports: {', '.join(refs)}." if refs else "")
        return ("Thanks for raising this. We have reviewed the report and it needs a "
                "change on our side, so we have raised a development ticket and linked "
                "it to this one." + cited +
                "\n\nWe will update this ticket as the work progresses.")

    def _developer_summary(self, retrieval, failures: list[str], component: str) -> str:
        lines = [f"Area: {component}."]
        if failures:
            lines.append("Reported symptoms: " + ", ".join(sorted(set(failures))[:6]) + ".")
        if retrieval.sops:
            lines.append(f"SOP '{retrieval.sops[0]['title']}' covers this symptom — "
                         f"work through its checks first.")
        if retrieval.similar_resolved:
            prior = ", ".join(f"{d['ref']} ({d.get('assignee') or 'unassigned'})"
                              for d in retrieval.similar_resolved[:3])
            lines.append(f"Closest prior fixes: {prior}.")
            hint = _resolution_hint(retrieval.similar_resolved[0])
            if hint:
                lines.append(f"What fixed it last time: {hint[:300]}")
        if retrieval.github:
            lines.append("Related code changes: " +
                         ", ".join(d["ref"] for d in retrieval.github[:3]) + ".")
        if not retrieval.similar_resolved and not retrieval.sops:
            lines.append("No precedent in the corpus — this looks novel; scope it before "
                         "estimating.")
        lines.append("Unknown: whether this reproduces outside the reported site/market.")
        return "\n".join(lines)


class ClaudeAnalyst:
    """The same job, done by Claude, with the evidence and rules.md in context."""

    name = "claude"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.env("ANALYST_MODEL", "claude-sonnet-5")
        self.fallback = HeuristicAnalyst()

    @staticmethod
    def available() -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return bool(config.env("ANTHROPIC_API_KEY"))

    def _evidence_block(self, retrieval) -> str:
        out = []
        for doc in retrieval.sops:
            out.append(f"[SOP {doc['ref']}] {doc['title']}\n{(doc.get('body') or '')[:1500]}")
        for doc in retrieval.similar_resolved:
            out.append(f"[RESOLVED {doc['ref']}] {doc['title']} "
                       f"(resolution={doc.get('resolution')}, by={doc.get('assignee')})\n"
                       f"{(doc.get('body') or '')[:1200]}")
        for doc in retrieval.github:
            out.append(f"[CODE {doc['ref']}] {doc['title']}")
        return "\n\n---\n\n".join(out) or "(no evidence found)"

    def analyse(self, issue: dict, retrieval, restated: str,
                rules_text: str = "") -> Analysis:
        try:
            import anthropic

            fields = issue.get("fields", {}) or {}
            comments = [c.get("body") or "" for c in
                        (fields.get("comment", {}) or {}).get("comments", [])]
            user_content = (
                f"Ticket: {issue['key']}\n"
                f"Summary: {fields.get('summary')}\n"
                f"Reporter: {((fields.get('reporter') or {}) or {}).get('displayName')}\n"
                f"Labels: {fields.get('labels')}\n\n"
                f"Description:\n{fields.get('description')}\n\n"
                f"Comments:\n" + ("\n---\n".join(comments) or "(none)") + "\n\n"
                f"One-sentence restatement from the sanity gate:\n{restated}\n\n"
                f"Candidate components: {retrieval.components}\n\n"
                f"Evidence:\n{self._evidence_block(retrieval)}"
            )
            system = SYSTEM_PROMPT
            if rules_text.strip():
                system += ("\n\nHuman-approved rules — these override your judgement:\n"
                           + rules_text.strip())

            client = anthropic.Anthropic()
            response = client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema",
                                          "schema": ANALYST_SCHEMA}},
                messages=[{"role": "user", "content": user_content}],
            )
            if getattr(response, "stop_reason", None) == "refusal":
                raise RuntimeError("model refused the request")
            text = next(b.text for b in response.content if b.type == "text")
            data = json.loads(text)
            usage = getattr(response, "usage", None)
            LOG.info("analysis.claude", ticket=issue["key"], model=self.model,
                     input_tokens=getattr(usage, "input_tokens", None),
                     output_tokens=getattr(usage, "output_tokens", None))
            return Analysis(
                classification=data["classification"],
                confidence=float(data["confidence"]),
                requirement_restated=data["requirement_restated"] or restated,
                proposed_comment=data["proposed_comment"],
                clone_summary=data["clone_summary"],
                developer_summary=data["developer_summary"],
                labels=list(data.get("labels") or []),
                flags=list(data.get("flags") or []),
                analyst=self.name,
            )
        except Exception as exc:
            LOG.warn("analysis.claude_failed", ticket=issue.get("key"),
                     error=str(exc)[:300], falling_back_to="heuristic")
            analysis = self.fallback.analyse(issue, retrieval, restated, rules_text)
            analysis.flags = list(analysis.flags) + ["analyst_fallback"]
            return analysis


class AnalystUnavailable(RuntimeError):
    """Asked for Claude explicitly, and it cannot run."""


def get_analyst(kind: str = "auto"):
    """`auto` degrades quietly; `claude` does not.

    ClaudeAnalyst keeps a HeuristicAnalyst as a fallback and swallows failures,
    so asking for it without a key used to run the heuristic and label the
    result "via claude" — a batch you would read believing a model wrote it.
    An explicit choice now fails loudly; only `auto` is allowed to degrade, and
    it reports the analyst it actually used.
    """
    if kind == "heuristic":
        return HeuristicAnalyst()
    if kind == "claude":
        if not ClaudeAnalyst.available():
            try:
                import anthropic  # noqa: F401
                why = "ANTHROPIC_API_KEY is not set in .env"
            except ImportError:
                why = "the anthropic package is not installed"
            raise AnalystUnavailable(
                f"--analyst claude was asked for but {why}. Use --analyst auto "
                "to fall back to the heuristic on purpose.")
        return ClaudeAnalyst()
    return ClaudeAnalyst() if ClaudeAnalyst.available() else HeuristicAnalyst()


def load_rules() -> str:
    """knowledge/rules.md — injected into every worker's context."""
    if config.RULES_MD.exists():
        return config.RULES_MD.read_text(encoding="utf-8")
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.jira_leader.analysis")
    ap.add_argument("ticket")
    ap.add_argument("--file", default="tests/fixtures/inbox.jsonl")
    ap.add_argument("--analyst", default="heuristic",
                    choices=["auto", "heuristic", "claude"])
    args = ap.parse_args(argv)

    from agents.historian.retrieval import Historian
    from agents.jira_leader.gates import restate

    issue = next(json.loads(l) for l in open(args.file, encoding="utf-8")
                 if l.strip() and json.loads(l)["key"] == args.ticket)
    sentence, _ = restate(issue)
    with Historian() as hist:
        f = issue["fields"]
        research = hist.research(issue["key"], f.get("summary") or "",
                                 f.get("description") or "")
        analysis = get_analyst(args.analyst).analyse(issue, research, sentence or "",
                                                     load_rules())
    print(json.dumps(analysis.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
