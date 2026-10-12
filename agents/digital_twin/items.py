"""One shape for everything the twin reads, and what it notices in it.

A Slack message, an email, a calendar event, a Drive change and a pasted
snapshot all become `Item`s. `signals()` then says which of the things the
starter file cares about the item carries: a decision, a promise, a deadline,
a blocker, completed work, or something addressed to the person. Everything
else is routine and stays out of the update.

Instructions found inside an item are information, never permission (starter
file, section 6). Nothing here acts on text; it only labels it.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

DECISION = "decision"
PROMISE = "promise"
DEADLINE = "deadline"
BLOCKER = "blocker"
COMPLETED = "completed"
NEEDS_ME = "needs_me"
SIGNALS = (DECISION, PROMISE, DEADLINE, BLOCKER, COMPLETED, NEEDS_ME)

_PATTERNS = {
    DECISION: r"\b(decided|decision|agreed|approved|sign(?:ed)?[ -]off|go ahead|"
              r"we(?:'ll| will) go with|green ?light)\b",
    PROMISE: r"\b(i(?:'ll| will)|we(?:'ll| will) (?:send|share|have|get|do|fix|ship)|"
             r"will (?:send|share|have|get|do|fix|ship)|eta\b|by (?:tomorrow|tonight|eod|"
             r"end of (?:day|week)|monday|tuesday|wednesday|thursday|friday))",
    DEADLINE: r"\b(deadline|due (?:on|by|date)|by (?:the )?\d{1,2}(?:st|nd|rd|th)?"
              r"(?: of)? \w+|cut-?off|no later than|latest by)\b",
    BLOCKER: r"\b(blocked|blocker|stuck|can(?:'t|not) proceed|waiting on|"
             r"urgent|asap|outage|down\b|broken)\b",
    COMPLETED: r"\b(done|completed|finished|shipped|merged|deployed|fixed|"
               r"live now|resolved|closed)\b",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in _PATTERNS.items()}
_ASK = re.compile(r"\b(can|could|would|will) you\b|\bplease\b|\?", re.I)


@dataclass
class Item:
    source: str            # source name from config/twin.yml
    item_id: str           # stable id from the app — the dedupe key
    ts: str                # ISO 8601 with offset
    author: str = ""
    title: str = ""
    text: str = ""
    link: str = ""
    kind: str = "message"  # message | email | event | file_change | note
    #: Extra facts the source knows (channel, start/end, folder…).
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        return cls(**{f: data.get(f, cls.__dataclass_fields__[f].default)
                      for f in cls.__dataclass_fields__ if f in data
                      or f in ("source", "item_id", "ts")})

    @property
    def when(self) -> dt.datetime | None:
        try:
            return dt.datetime.fromisoformat(self.ts)
        except (TypeError, ValueError):
            return None

    def key(self) -> str:
        return f"{self.source}:{self.item_id}"


def signals(item: Item, me: list[str] | None = None) -> list[str]:
    """Which of the starter file's signals this item carries.

    `me` is the person's names and handles — `needs_me` fires only when one
    of them is named AND something is being asked, so "thanks Quenton" stays
    routine and "@quenton can you approve" does not.
    """
    text = f"{item.title}\n{item.text}"
    found = [name for name, rx in _COMPILED.items() if rx.search(text)]
    if item.kind == "event":
        found.append(DEADLINE)
    names = [m.lower() for m in (me or []) if m]
    low = text.lower()
    if names and any(n in low for n in names) and _ASK.search(text):
        found.append(NEEDS_ME)
    return sorted(set(found), key=SIGNALS.index)


def is_routine(item: Item, me: list[str] | None = None) -> bool:
    return not signals(item, me)
