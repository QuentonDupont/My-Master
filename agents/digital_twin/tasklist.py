"""The Task List — Task | Next step | Owner | Due date | Status | Source.

Starter file, section 3. Two rules the code enforces rather than trusts:

* a promise is kept separate from a proposed date (`due_kind`), so "I'll try
  for Friday" never reads as a commitment;
* a task is Done only with proof — a link, a message, a ticket — because
  "should be done by now" is how things get dropped.

    python -m agents.digital_twin.tasklist add "Send RMA fix note" --owner me --due 2026-09-26
    python -m agents.digital_twin.tasklist show
    python -m agents.digital_twin.tasklist done t_1a2b --proof https://...
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core import config, log

LOG = log.get("digital_twin")

TO_DO = "To do"
IN_PROGRESS = "In progress"
WAITING = "Waiting"
NEEDS_DECISION = "Needs my decision"
DONE = "Done"
STATES = (TO_DO, IN_PROGRESS, WAITING, NEEDS_DECISION, DONE)

PROMISE = "promise"
PROPOSED = "proposed"
DUE_KINDS = (PROMISE, PROPOSED)


class NoProof(ValueError):
    pass


@dataclass
class Task:
    task_id: str
    task: str
    next_step: str = ""
    owner: str = "me"
    due: str = ""                 # ISO date
    due_kind: str = PROPOSED      # promise | proposed
    status: str = TO_DO
    #: Where this came from — a link, a message, a ticket, or "you said so".
    source: str = ""
    proof: str = ""               # required for Done
    created: str = ""
    updated: str = ""
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(**{f: data.get(f) for f in cls.__dataclass_fields__ if f in data})

    def overdue(self, today: dt.date | None = None) -> bool:
        if not self.due or self.status == DONE:
            return False
        try:
            return dt.date.fromisoformat(self.due) < (today or dt.date.today())
        except ValueError:
            return False


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return "t_" + uuid.uuid4().hex[:6]


def store() -> Path:
    p = config.TWIN_DIR / "tasks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save(task: Task) -> Path:
    task.updated = _now()
    path = store() / f"{task.task_id}.json"
    path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def load(task_id: str) -> Task:
    return Task.from_dict(json.loads(
        (store() / f"{task_id}.json").read_text(encoding="utf-8")))


def all_tasks() -> list[Task]:
    return [Task.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(store().glob("t_*.json"))]


def open_tasks() -> list[Task]:
    return [t for t in all_tasks() if t.status != DONE]


def add(task: str, *, next_step: str = "", owner: str = "me", due: str = "",
        due_kind: str = PROPOSED, source: str = "", status: str = TO_DO) -> Task:
    if status not in STATES:
        raise ValueError(f"status must be one of {STATES}")
    if due_kind not in DUE_KINDS:
        raise ValueError(f"due_kind must be one of {DUE_KINDS}")
    if due:
        dt.date.fromisoformat(due)  # raises on a non-date
    t = Task(task_id=new_id(), task=task, next_step=next_step, owner=owner,
             due=due, due_kind=due_kind, source=source, status=status,
             created=_now())
    t.history.append({"ts": t.created, "change": "created", "source": source})
    save(t)
    LOG.info("task.added", task=t.task_id, owner=owner, due=due, kind=due_kind)
    return t


def update(task_id: str, *, reason: str = "", **changes) -> Task:
    """Change fields, keeping the source of the change. Done needs proof."""
    t = load(task_id)
    if changes.get("status") == DONE and not (changes.get("proof") or t.proof):
        raise NoProof(f"{task_id}: Done needs proof — a link, a message or a ticket")
    if "status" in changes and changes["status"] not in STATES:
        raise ValueError(f"status must be one of {STATES}")
    if "due_kind" in changes and changes["due_kind"] not in DUE_KINDS:
        raise ValueError(f"due_kind must be one of {DUE_KINDS}")
    for key, value in changes.items():
        if key not in t.__dataclass_fields__ or key in ("task_id", "history"):
            raise KeyError(key)
        before = getattr(t, key)
        if before != value:
            t.history.append({"ts": _now(), "field": key, "was": before,
                              "became": value, "reason": reason})
            setattr(t, key, value)
    save(t)
    return t


def done(task_id: str, proof: str, reason: str = "") -> Task:
    return update(task_id, status=DONE, proof=proof, reason=reason)


def render(tasks: list[Task] | None = None, today: dt.date | None = None) -> str:
    tasks = all_tasks() if tasks is None else tasks
    today = today or dt.date.today()
    out = ["TASK LIST", "", "Task | Next step | Owner | Due | Status | Source"]
    if not tasks:
        out.append("(nothing tracked)")
    order = {s: i for i, s in enumerate((NEEDS_DECISION, IN_PROGRESS, TO_DO, WAITING, DONE))}
    for t in sorted(tasks, key=lambda t: (order.get(t.status, 9), t.due or "9999")):
        due = t.due or "-"
        if t.due:
            due += " (promise)" if t.due_kind == PROMISE else " (proposed)"
            if t.overdue(today):
                due += " OVERDUE"
        line = f"{t.task} | {t.next_step or '-'} | {t.owner} | {due} | {t.status} | {t.source or '-'}"
        out.append(f"{t.task_id}  {line}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Task List.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("task")
    a.add_argument("--next", dest="next_step", default="")
    a.add_argument("--owner", default="me")
    a.add_argument("--due", default="", help="ISO date")
    a.add_argument("--promise", action="store_true",
                   help="the date is a promise, not a proposal")
    a.add_argument("--source", default="")
    a.add_argument("--status", default=TO_DO, choices=STATES)
    sub.add_parser("show")
    u = sub.add_parser("set", help="change one field")
    u.add_argument("task_id")
    u.add_argument("field")
    u.add_argument("value")
    u.add_argument("--reason", default="")
    d = sub.add_parser("done")
    d.add_argument("task_id")
    d.add_argument("--proof", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "add":
        t = add(args.task, next_step=args.next_step, owner=args.owner, due=args.due,
                due_kind=PROMISE if args.promise else PROPOSED,
                source=args.source, status=args.status)
        print(t.task_id)
        return 0
    if args.cmd == "show":
        print(render())
        return 0
    if args.cmd == "set":
        update(args.task_id, reason=args.reason, **{args.field: args.value})
        print(render())
        return 0
    if args.cmd == "done":
        done(args.task_id, args.proof)
        print(render())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
