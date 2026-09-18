"""Per-thread worker: a mention in, a reply proposal out. Posts nothing.

Mirrors the Jira worker's loop, because the failure modes are the same:

  1 read the thread -> 2 sanity gate -> 3 never-touch gate ->
  4 does a ticket already cover this? -> 5 Historian retrieval ->
  6 decide -> 7 draft the reply. Nothing is sent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from agents.historian.retrieval import Historian
from agents.jira_leader import description as description_mod
from agents.jira_leader.gates import never_touch, restate
from core import config, ledger as ledger_mod, log, slack_proposals

LOG = log.get("slack_worker")

MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
LINK_RE = re.compile(r"<(https?://[^|>]+)(\|[^>]*)?>")
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
#: history worth naming in public — weaker than that and it is a guess
ANSWER_MIN_SIMILARITY = 0.45


@dataclass
class Outcome:
    thread_key: str
    state: str
    proposal_id: str | None
    reason: str

    def to_dict(self) -> dict:
        return {"thread": self.thread_key, "state": self.state,
                "proposal_id": self.proposal_id, "reason": self.reason}


def clean(text: str) -> str:
    """Slack markup out, so the gates and the Historian see plain words."""
    text = MENTION_RE.sub(" ", text or "")
    text = LINK_RE.sub(lambda m: m.group(1), text)
    return " ".join(text.split())


def question_of(messages: list[dict], bot_user_id: str | None = None) -> str:
    """The thread's question: the message that mentioned us, plus what led to it."""
    asked = [m for m in messages
             if not bot_user_id or f"<@{bot_user_id}>" in (m.get("text") or "")]
    target = asked[-1] if asked else (messages[-1] if messages else {})
    context = [clean(m.get("text") or "") for m in messages[:6]]
    return " ".join([clean(target.get("text") or ""), *context]).strip()


def _as_issue(channel_name: str, text: str, asked_by_name: str) -> dict:
    """Shape a thread like a ticket so the existing gates apply unchanged."""
    return {"key": "SLACK-0",
            "fields": {"summary": text[:250], "description": text,
                       "labels": [], "components": [],
                       "reporter": {"displayName": asked_by_name},
                       "created": "", "comment": {"comments": []}}}


def process(mention: dict, *, ledger: ledger_mod.Ledger | None = None,
            historian: Historian | None = None, reader=None,
            bot_user_id: str | None = None) -> Outcome:
    """Handle one mention. `mention` carries channel, thread_ts and messages."""
    channel = mention["channel"]
    thread_ts = mention["thread_ts"]
    thread_key = ledger_mod.SlackLedger.key(channel, thread_ts)
    messages = mention.get("messages") or []
    channel_name = mention.get("channel_name") or channel
    asked_by = mention.get("user") or ""
    asked_by_name = mention.get("user_name") or asked_by

    own_ledger = ledger is None
    own_hist = historian is None
    led = ledger or ledger_mod.Ledger()
    slack_led = ledger_mod.SlackLedger(led)
    hist = historian or Historian()
    try:
        text = question_of(messages, bot_user_id)
        issue = _as_issue(channel_name, text, asked_by_name)

        def finish(proposal, state, reason):
            problems = slack_proposals.validate(proposal.to_dict())
            if problems:
                LOG.error("slack_worker.invalid", thread=thread_key, problems=problems)
                proposal.flags = list(proposal.flags) + ["invalid_proposal"]
            slack_proposals.save(proposal)
            slack_led.transition(thread_key, state, proposal_id=proposal.proposal_id)
            LOG.info("slack_worker.done", thread=thread_key, state=state,
                     kind=proposal.kind)
            return Outcome(thread_key, state, proposal.proposal_id, reason)

        def new(**kw):
            return slack_proposals.SlackProposal(
                proposal_id=slack_proposals.next_id(), channel=channel,
                channel_name=channel_name, thread_ts=thread_ts,
                permalink=mention.get("permalink", ""), asked_by=asked_by,
                asked_by_name=asked_by_name, **kw)

        # 2. sanity gate — can the question be stated in one sentence?
        restated, why = restate(issue)
        if not restated:
            proposal = new(question_restated=f"Unclear — {why}.",
                           kind=slack_proposals.ESCALATE, confidence=0.0,
                           flags=["sanity_gate"])
            return finish(proposal, ledger_mod.ESCALATED, f"sanity gate: {why}")

        # 3. never-touch — the system says nothing at all about these
        hits = never_touch(issue)
        if hits:
            categories = sorted({h["category"] for h in hits})
            proposal = new(question_restated=restated,
                           kind=slack_proposals.ESCALATE, confidence=0.0,
                           flags=["never_touch"]
                                 + [f"never_touch:{c}" for c in categories])
            return finish(proposal, ledger_mod.ESCALATED,
                          f"never-touch: {', '.join(categories)}")

        # 4-5. what does the Historian know?
        research = hist.research("SLACK-0", restated, text)
        named = [k for k in TICKET_RE.findall(text)
                 if k.split("-")[0] in config.allowed_projects()]
        # Only a STRONG duplicate justifies telling a channel "there is already a
        # ticket for this". The weaker "possibly related" signal is a hint for a
        # human reading a proposal, not an assertion to make in public.
        existing = research.duplicates[:1]
        procedures = description_mod.relevant_procedures(research, text, limit=1)
        useful_history = [d for d in research.similar_resolved
                          if d.get("similarity", 0) >= ANSWER_MIN_SIMILARITY][:2]

        evidence = [{"ref": e["ref"], "why": e["why"]}
                    for e in research.evidence(limit=4)]

        # 6-7. decide and draft
        if existing:
            top = existing[0]
            clones = ", ".join(c["key"] for c in (top.get("open_clones") or []))
            reply = (f"There's already a ticket for this: {top['ref']} — "
                     f"{top['title'][:90]} ({top.get('status')})."
                     + (f" Development is tracked in {clones}." if clones else "")
                     + "\n\nFollowing up there keeps the history in one place.")
            kind = slack_proposals.POINT_AT_TICKET
            confidence = round(min(0.5 + float(top.get("similarity", 0)) / 2, 0.9), 2)
        elif procedures or useful_history:
            reply = _answer(procedures, useful_history)
            kind = slack_proposals.ANSWER
            best = (procedures or useful_history)[0]
            confidence = round(min(0.35 + float(best.get("similarity", 0)), 0.9), 2)
        else:
            reply = ("I don't have anything on this yet. Raise it on "
                     f"{config.intake_project()} and it'll be picked up in triage.")
            kind = slack_proposals.ASK_FOR_TICKET
            confidence = 0.4

        flags = []
        if confidence < 0.5:
            flags.append("low_confidence")
        if named:
            flags.append("thread_names_a_ticket")
        proposal = new(question_restated=restated, kind=kind, confidence=confidence,
                       proposed_reply=reply, evidence=evidence, flags=flags)
        return finish(proposal, ledger_mod.PROPOSED, f"{kind} for {thread_key}")
    except Exception as exc:
        LOG.error("slack_worker.crashed", thread=thread_key, error=str(exc))
        slack_led.record_error(thread_key, f"worker: {exc}")
        try:
            slack_led.transition(thread_key, ledger_mod.NEW)
        except ledger_mod.LedgerError:
            pass
        return Outcome(thread_key, "ERROR", None, str(exc))
    finally:
        if own_hist:
            hist.close()
        if own_ledger:
            led.close()


def _answer(procedures: list[dict], history: list[dict]) -> str:
    """Short, and links rather than quotes.

    Internal ticket text is not pasted into a channel: it carries other teams'
    words, keys from boards out of scope, and half-finished thinking. A link
    lets the reader see it in context, with its permissions intact.
    """
    lines = []
    for doc in procedures:
        url = doc.get("url") or ""
        lines.append(f"This is covered by *{doc['title']}*"
                     + (f" — {url}" if str(url).startswith("http") else "") + ".")
    if history:
        refs = ", ".join(d["ref"] for d in history)
        lines.append(f"Same thing came up in {refs} — worth a look.")
    lines.append("If that doesn't cover it, say so and I'll pass it to triage.")
    return "\n\n".join(lines)
