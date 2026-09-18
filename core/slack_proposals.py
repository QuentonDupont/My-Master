"""Reply proposals — the Slack equivalent of core/proposals.py.

A Slack message can be deleted but not unread, so it gets the same control a
PESD1 comment does: the Slack Leader drafts, a human approves, and only then
does `core.slack_execute` post it.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core import config

ANSWER = "ANSWER"          # the Historian knows this; reply with the answer
POINT_AT_TICKET = "POINT_AT_TICKET"   # a ticket already covers it; link it
ASK_FOR_TICKET = "ASK_FOR_TICKET"     # this needs a PESD1 ticket; ask for one
ESCALATE = "ESCALATE"      # never-touch, or nothing sensible to say: stay silent
KINDS = (ANSWER, POINT_AT_TICKET, ASK_FOR_TICKET, ESCALATE)

STORE = lambda: config.REVIEW_DIR / "slack_proposals"  # noqa: E731
ID_RE = re.compile(r"^s_\d{4,}$")


class ValidationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class SlackProposal:
    proposal_id: str
    channel: str
    channel_name: str
    thread_ts: str
    permalink: str
    asked_by: str                  # Slack user id
    asked_by_name: str
    question_restated: str
    kind: str
    confidence: float
    proposed_reply: str = ""
    evidence: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "SlackProposal":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        known["evidence"] = list(data.get("evidence") or [])
        known["flags"] = list(data.get("flags") or [])
        known["confidence"] = float(data.get("confidence") or 0.0)
        return cls(**known)

    def validate(self) -> "SlackProposal":
        problems = validate(self.to_dict())
        if problems:
            raise ValidationError(problems)
        return self


def validate(data: dict) -> list[str]:
    p: list[str] = []
    if not ID_RE.match(str(data.get("proposal_id", ""))):
        p.append("proposal_id must look like s_0001")
    for required in ("channel", "thread_ts"):
        if not str(data.get(required) or "").strip():
            p.append(f"{required} is empty")
    if not str(data.get("question_restated") or "").strip():
        p.append("question_restated is empty — the sanity gate should have escalated")
    kind = data.get("kind")
    if kind not in KINDS:
        p.append(f"kind {kind!r} not in {KINDS}")
    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
        p.append("confidence must be a number in [0, 1]")
    reply = (data.get("proposed_reply") or "").strip()
    if kind == ESCALATE and reply:
        p.append("ESCALATE must carry no reply — the system stays silent")
    if kind in (ANSWER, POINT_AT_TICKET, ASK_FOR_TICKET) and not reply:
        p.append(f"{kind} requires a proposed_reply")
    if len(reply) > 2800:
        p.append("proposed_reply is too long for a Slack message")
    for i, ev in enumerate(data.get("evidence") or []):
        if not ev.get("ref"):
            p.append(f"evidence[{i}].ref is empty")
        if not ev.get("why"):
            p.append(f"evidence[{i}].why is empty")
    for secret in config.known_secrets():
        if secret and len(secret) >= 8 and secret in reply:
            p.append("reply contains a credential (invariant 7)")
            break
    # A reply that names a ticket must name a real one on an allowed board.
    for key in re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", reply):
        if key.split("-")[0] not in config.allowed_projects():
            p.append(f"reply names {key}, outside the boards in scope")
    return p


def store_dir() -> Path:
    path = STORE()
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_id() -> str:
    import os

    d = store_dir()
    used = [int(m.group(1)) for f in d.glob("s_*.json")
            if (m := re.match(r"s_(\d+)\.json$", f.name))]
    n = max(used, default=0)
    while True:
        n += 1
        pid = f"s_{n:04d}"
        try:
            fd = os.open(d / f"{pid}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        return pid


def save(proposal: SlackProposal) -> Path:
    path = store_dir() / f"{proposal.proposal_id}.json"
    path.write_text(proposal.to_json(), encoding="utf-8")
    return path


def load(proposal_id: str) -> SlackProposal:
    path = store_dir() / f"{proposal_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no slack proposal {proposal_id}")
    return SlackProposal.from_dict(json.loads(path.read_text(encoding="utf-8")))


def load_all() -> list[SlackProposal]:
    out = []
    for f in sorted(store_dir().glob("s_*.json")):
        if f.stat().st_size:
            out.append(SlackProposal.from_dict(json.loads(f.read_text(encoding="utf-8"))))
    return out
