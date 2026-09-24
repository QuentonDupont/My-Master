"""Drafts — messages written in the person's voice, never sent by the twin.

Starter file, section 5. A draft is a file in twin/drafts. The person sends it
themselves, from the app, under their own name; then `sent` records the proof
(a link or a message id) so the record shows what actually went out. There is
no send function in this module and nothing to construct one with.

    python -m agents.digital_twin.drafts new slack "#ops" "ask Unni for the RMA ETA"
    python -m agents.digital_twin.drafts list
    python -m agents.digital_twin.drafts sent d_1a2b3c --proof https://pomelo.slack.com/...
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agents.digital_twin import profile as profile_mod, tasklist
from core import config, log

LOG = log.get("digital_twin")

DRAFTED = "DRAFTED"
APPROVED = "APPROVED"     # the person said yes; still theirs to send
SENT = "SENT"             # recorded with proof, after they sent it
DROPPED = "DROPPED"
STATES = (DRAFTED, APPROVED, SENT, DROPPED)
CHANNELS = ("slack", "email", "jira", "note")


@dataclass
class Draft:
    draft_id: str
    channel: str          # slack | email | jira | note
    to: str               # channel name, address, ticket key
    ask: str              # what the person wanted said, verbatim
    text: str             # the draft itself
    state: str = DRAFTED
    created: str = ""
    proof: str = ""
    task_id: str = ""     # task this draft moves, if any
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def store() -> Path:
    p = config.TWIN_DIR / "drafts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save(d: Draft) -> Path:
    path = store() / f"{d.draft_id}.json"
    path.write_text(json.dumps(d.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load(draft_id: str) -> Draft:
    return Draft(**json.loads((store() / f"{draft_id}.json").read_text(encoding="utf-8")))


def all_drafts() -> list[Draft]:
    return [Draft(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(store().glob("d_*.json"))]


def compose(ask: str, profile, channel: str = "slack") -> str:
    """One main ask, short, in the person's measured style. This is the
    heuristic drafter; a model-backed one slots in here and nowhere else."""
    style = profile.writing_style if profile.writing_style != profile_mod.NOT_KNOWN else ""
    body = ask.strip().rstrip(".")
    if body and body[0].islower() and "all lower case" not in style:
        body = body[0].upper() + body[1:]
    lines = []
    if "opens with a greeting" in style:
        lines.append("Hi," if channel == "email" else "Hi,")
    if not body.endswith("?"):
        first = body.split()[0].lower() if body.split() else ""
        body += "?" if first in ("can", "could", "would", "will", "what", "when",
                                 "where", "who", "why", "how", "is", "are", "do",
                                 "does", "did", "any") else "."
    lines.append(body)
    if "signs off with thanks" in style:
        lines.append("Thanks")
    text = "\n".join(lines)
    if "all lower case" in style:
        text = text.lower()
    return text


def new(channel: str, to: str, ask: str, task_id: str = "") -> Draft:
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    profile = profile_mod.load()
    d = Draft(draft_id="d_" + uuid.uuid4().hex[:6], channel=channel, to=to,
              ask=ask, text=compose(ask, profile, channel), task_id=task_id,
              created=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    if not profile.style_samples:
        d.notes.append("No writing samples on file — drafted plain; share two "
                       "messages you wrote and this will read more like you.")
    if len(ask.split()) > 80:
        d.notes.append("Long ask — link supporting work instead of pasting it.")
    save(d)
    LOG.info("draft.new", draft=d.draft_id, channel=channel, to=to)
    return d


def approve(draft_id: str, text: str | None = None) -> Draft:
    d = load(draft_id)
    if text is not None and text != d.text:
        d.notes.append(f"edited before approval; was: {d.text}")
        d.text = text
    d.state = APPROVED
    save(d)
    return d


def sent(draft_id: str, proof: str) -> Draft:
    """The person sent it. Record where, and close any linked task."""
    if not proof.strip():
        raise ValueError("proof is required — a link or message id")
    d = load(draft_id)
    d.state, d.proof = SENT, proof
    save(d)
    if d.task_id:
        try:
            tasklist.done(d.task_id, proof, reason=f"draft {d.draft_id} sent")
        except FileNotFoundError:
            d.notes.append(f"task {d.task_id} not found")
            save(d)
    LOG.info("draft.sent", draft=d.draft_id, proof=proof)
    return d


def drop(draft_id: str, why: str = "") -> Draft:
    d = load(draft_id)
    d.state = DROPPED
    if why:
        d.notes.append(why)
    save(d)
    return d


def render(drafts: list[Draft] | None = None) -> str:
    drafts = all_drafts() if drafts is None else drafts
    out = ["DRAFTS (yours to send — the twin never does)", ""]
    if not drafts:
        out.append("(none)")
    for d in drafts:
        out.append(f"{d.draft_id}  {d.state:8} {d.channel:6} -> {d.to}")
        out += [f"    {line}" for line in d.text.splitlines()]
        if d.proof:
            out.append(f"    proof: {d.proof}")
        for n in d.notes:
            out.append(f"    note: {n}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drafts, never sent by the twin.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("channel", choices=CHANNELS)
    n.add_argument("to")
    n.add_argument("ask")
    n.add_argument("--task", default="")
    sub.add_parser("list")
    a = sub.add_parser("approve")
    a.add_argument("draft_id")
    a.add_argument("--text", default=None, help="the edited final text")
    s = sub.add_parser("sent")
    s.add_argument("draft_id")
    s.add_argument("--proof", required=True)
    x = sub.add_parser("drop")
    x.add_argument("draft_id")
    x.add_argument("--why", default="")
    args = ap.parse_args(argv)

    if args.cmd == "new":
        d = new(args.channel, args.to, args.ask, args.task)
        print(render([d]))
        return 0
    if args.cmd == "list":
        print(render())
        return 0
    if args.cmd == "approve":
        print(render([approve(args.draft_id, args.text)]))
        return 0
    if args.cmd == "sent":
        print(render([sent(args.draft_id, args.proof)]))
        return 0
    if args.cmd == "drop":
        drop(args.draft_id, args.why)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
