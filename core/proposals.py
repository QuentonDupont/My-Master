"""Proposal schema, validation, serialisation and the on-disk proposal store.

A proposal is the only thing a worker produces. Nothing else may reach Jira.
`validate()` is deliberately strict: a proposal that does not validate cannot be
approved, and therefore cannot be executed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core import config

ANSWERABLE = "ANSWERABLE"
NEEDS_CODE = "NEEDS_CODE"
ESCALATE = "ESCALATE"
DUPLICATE = "DUPLICATE"
CLASSIFICATIONS = (ANSWERABLE, NEEDS_CODE, ESCALATE, DUPLICATE)

SOURCES = ("sheet", "jira_field", "unknown")
CONFIDENCES = ("high", "medium", "unknown")
EVIDENCE_TYPES = ("jira", "confluence", "github", "sop")

PROPOSAL_ID_RE = re.compile(r"^p_\d{4,}$")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

STORE = config.REVIEW_DIR / "proposals"


class ValidationError(ValueError):
    """Raised with every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class Requester:
    email: str | None = None
    source: str = "unknown"
    confidence: str = "unknown"
    matched_row: dict = field(default_factory=dict)


@dataclass
class Evidence:
    type: str
    ref: str
    why: str


@dataclass
class Clone:
    target_project: str
    summary: str
    description: str
    assignee: str | None = None
    assignee_reason: str = ""
    assignee_alternates: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    priority: str = "Medium"
    link_type: str = "is cloned by"


@dataclass
class Proposal:
    proposal_id: str
    ticket: str
    ticket_url: str
    requirement_restated: str
    requester: Requester
    classification: str
    confidence: float
    proposed_comment: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    clone: Clone | None = None
    pesd1_transition: str | None = None
    flags: list[str] = field(default_factory=list)

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        if self.clone is None:
            data["clone"] = None
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Proposal":
        req = data.get("requester") or {}
        clone = data.get("clone")
        return cls(
            proposal_id=data["proposal_id"],
            ticket=data["ticket"],
            ticket_url=data.get("ticket_url") or config.ticket_url(data["ticket"]),
            requirement_restated=data.get("requirement_restated", ""),
            requester=Requester(
                email=req.get("email"),
                source=req.get("source", "unknown"),
                confidence=req.get("confidence", "unknown"),
                matched_row=req.get("matched_row") or {},
            ),
            classification=data.get("classification", ""),
            confidence=float(data.get("confidence") or 0.0),
            proposed_comment=data.get("proposed_comment") or "",
            evidence=[Evidence(**e) for e in (data.get("evidence") or [])],
            clone=Clone(**clone) if clone else None,
            pesd1_transition=data.get("pesd1_transition"),
            flags=list(data.get("flags") or []),
        )

    @classmethod
    def from_json(cls, text: str) -> "Proposal":
        return cls.from_dict(json.loads(text))

    # -- validation --------------------------------------------------------
    def validate(self) -> "Proposal":
        problems = validate(self.to_dict())
        if problems:
            raise ValidationError(problems)
        return self


def validate(data: dict) -> list[str]:  # noqa: C901 - a checklist, kept flat on purpose
    p: list[str] = []
    allowed = config.allowed_projects()
    intake, dev = config.intake_project(), config.dev_project()

    pid = data.get("proposal_id", "")
    if not PROPOSAL_ID_RE.match(str(pid)):
        p.append(f"proposal_id {pid!r} must look like p_0142")

    ticket = str(data.get("ticket", ""))
    if not TICKET_RE.match(ticket):
        p.append(f"ticket {ticket!r} is not a Jira key")
    elif ticket.split("-")[0] != intake:
        p.append(f"ticket {ticket} is not on the intake board {intake}")

    url = str(data.get("ticket_url", ""))
    if ticket and not url.endswith(f"/browse/{ticket}"):
        p.append("ticket_url must be the browse URL of `ticket`")

    req_text = (data.get("requirement_restated") or "").strip()
    if not req_text:
        p.append("requirement_restated is empty (sanity gate should have escalated)")
    elif len(req_text) > 400:
        p.append("requirement_restated must be one sentence (<=400 chars)")

    requester = data.get("requester") or {}
    source = requester.get("source")
    confidence = requester.get("confidence")
    if source not in SOURCES:
        p.append(f"requester.source {source!r} not in {SOURCES}")
    if confidence not in CONFIDENCES:
        p.append(f"requester.confidence {confidence!r} not in {CONFIDENCES}")
    email = requester.get("email")
    if source == "unknown" and email:
        p.append("requester.source is unknown but an email is set — never guess (rule 6)")
    if email and "@" not in str(email):
        p.append(f"requester.email {email!r} is not an email")
    if confidence == "medium" and not requester.get("matched_row"):
        p.append("medium-confidence requester match must carry matched_row")
    if (source == "unknown" or not email) and "requester_unknown" not in (data.get("flags") or []):
        p.append("unknown requester must add the 'requester_unknown' flag")

    cls = data.get("classification")
    if cls not in CLASSIFICATIONS:
        p.append(f"classification {cls!r} not in {CLASSIFICATIONS}")

    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
        p.append("confidence must be a number in [0, 1]")

    for i, ev in enumerate(data.get("evidence") or []):
        if ev.get("type") not in EVIDENCE_TYPES:
            p.append(f"evidence[{i}].type {ev.get('type')!r} not in {EVIDENCE_TYPES}")
        if not ev.get("ref"):
            p.append(f"evidence[{i}].ref is empty")
        if not ev.get("why"):
            p.append(f"evidence[{i}].why is empty — evidence must say why it is relevant")

    comment = data.get("proposed_comment") or ""
    clone = data.get("clone")

    if cls == ESCALATE:
        if comment.strip():
            p.append("ESCALATE must carry no proposed_comment (invariant 3)")
        if clone:
            p.append("ESCALATE must carry no clone (invariant 3)")
        if data.get("pesd1_transition"):
            p.append("ESCALATE must not transition PESD1")
    if cls == DUPLICATE and clone:
        p.append("a duplicate is never cloned")
    if cls == NEEDS_CODE and not clone:
        p.append("NEEDS_CODE requires a clone block")
    if cls in (ANSWERABLE, DUPLICATE) and clone:
        p.append(f"{cls} must not carry a clone")
    if cls in (ANSWERABLE, NEEDS_CODE) and not comment.strip():
        p.append(f"{cls} requires a proposed_comment")

    if clone:
        target = clone.get("target_project")
        if target != dev:
            p.append(f"clone.target_project {target!r} must be {dev}")
        if target not in allowed:
            p.append(f"clone.target_project {target!r} is outside allowed_projects")
        if not (clone.get("summary") or "").strip():
            p.append("clone.summary is empty")
        desc = clone.get("description") or ""
        if not desc.strip():
            p.append("clone.description is empty")
        elif "{quote}" not in desc and "---" not in desc:
            # The requester's own words must reach the developer unedited,
            # separated from anything the system wrote about them.
            p.append("clone.description must carry the request verbatim, quoted "
                     "and separated from the summary")
        elif len(desc) < 120:
            p.append("clone.description is too thin to work from")
        alternates = clone.get("assignee_alternates")
        if not isinstance(alternates, list) or not alternates:
            p.append("clone.assignee_alternates is required — give a ranked shortlist")
        if clone.get("assignee") and not (clone.get("assignee_reason") or "").strip():
            p.append("clone.assignee without assignee_reason")
        if not isinstance(clone.get("labels"), list):
            p.append("clone.labels must be a list")
        if not clone.get("priority"):
            p.append("clone.priority is empty")
        if not clone.get("link_type"):
            p.append("clone.link_type is empty")
        expected_transition = config.boards()["intake"]["dev_transition"]
        if data.get("pesd1_transition") != expected_transition:
            p.append(f"pesd1_transition must be {expected_transition!r} when cloning")

    if not isinstance(data.get("flags"), list):
        p.append("flags must be a list")

    for secret in config.known_secrets():
        if secret and len(secret) >= 8 and (secret in comment or secret in json.dumps(clone or {})):
            p.append("proposal text contains a credential (invariant 7)")
            break

    return p


# -- store -----------------------------------------------------------------
def store_dir() -> Path:
    STORE.mkdir(parents=True, exist_ok=True)
    return STORE


def next_id() -> str:
    """Reserve the next p_NNNN. Safe across concurrent workers via O_EXCL."""
    d = store_dir()
    used = [int(m.group(1)) for f in d.glob("p_*.json")
            if (m := re.match(r"p_(\d+)\.json$", f.name))]
    n = max(used, default=0)
    while True:
        n += 1
        pid = f"p_{n:04d}"
        try:
            fd = os.open(d / f"{pid}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        return pid


def path_for(proposal_id: str) -> Path:
    return store_dir() / f"{proposal_id}.json"


def save(proposal: Proposal) -> Path:
    path = path_for(proposal.proposal_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(proposal.to_json(), encoding="utf-8")
    tmp.replace(path)
    return path


def load(proposal_id: str) -> Proposal:
    path = path_for(proposal_id)
    if not path.exists():
        raise FileNotFoundError(f"no proposal {proposal_id} in {store_dir()}")
    return Proposal.from_json(path.read_text(encoding="utf-8"))


def load_all() -> list[Proposal]:
    out = []
    for f in sorted(store_dir().glob("p_*.json")):
        if f.stat().st_size == 0:  # reserved id, not yet written
            continue
        out.append(Proposal.from_json(f.read_text(encoding="utf-8")))
    return out


# -- correction diffing ----------------------------------------------------
def diff(before: dict, after: dict, prefix: str = "") -> list[tuple[str, Any, Any]]:
    """Flat field-path diff used to write knowledge/corrections.jsonl."""
    changes: list[tuple[str, Any, Any]] = []
    keys = sorted(set(before) | set(after))
    for key in keys:
        path = f"{prefix}{key}"
        b, a = before.get(key), after.get(key)
        if isinstance(b, dict) and isinstance(a, dict):
            changes.extend(diff(b, a, prefix=f"{path}."))
        elif b != a:
            changes.append((path, b, a))
    return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.proposals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_v = sub.add_parser("validate", help="validate a proposal json file (or - for stdin)")
    p_v.add_argument("path")
    p_s = sub.add_parser("show", help="print a stored proposal")
    p_s.add_argument("proposal_id")
    sub.add_parser("list", help="list stored proposals")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text()
        problems = validate(json.loads(text))
        if problems:
            print("INVALID")
            for prob in problems:
                print(f"  - {prob}")
            return 1
        print("VALID")
    elif args.cmd == "show":
        print(load(args.proposal_id).to_json())
    elif args.cmd == "list":
        for prop in load_all():
            print(f"{prop.proposal_id}  {prop.ticket:<14} {prop.classification:<11} "
                  f"conf={prop.confidence:.2f}  {prop.requirement_restated[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
