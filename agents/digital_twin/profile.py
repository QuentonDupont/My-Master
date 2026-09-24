"""The Work Profile — who the person is, what they are working towards, and
which sources the twin has actually been allowed to read.

Starter file, section 3. Missing facts stay "not known": the profile records
what the person said, never what the twin assumed. The source list is the one
place that says whether an app is working, blocked or not enabled, and only a
passed test (`sources.test`) moves a source to "working".

    python -m agents.digital_twin.profile setup      # one question at a time
    python -m agents.digital_twin.profile show
    python -m agents.digital_twin.profile set role "Technical PM"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core import config, log

LOG = log.get("digital_twin")

NOT_KNOWN = "not known"

WORKING = "working"
BLOCKED = "blocked"
NOT_ENABLED = "not enabled"
SOURCE_STATES = (WORKING, BLOCKED, NOT_ENABLED)

#: The setup questions, in the order the starter file asks them. One at a
#: time, short answers, any of them skippable.
QUESTIONS = [
    ("name", "What is your name?"),
    ("role", "What is your role, and what work are you responsible for?"),
    ("goals", "What are your top three goals right now? (one per line, blank line to finish)"),
    ("projects", "What projects and deadlines should we track? (one per line)"),
    ("people", "Who do you work with, and what does each handle? (Name - what they handle, one per line)"),
    ("writing_style", "How do you like to write? Paste one or two messages as examples."),
    ("tools", "Which files or work apps may I use? (one per line)"),
    ("may_do", "What may I do on my own? (one per line)"),
    ("needs_ok", "What needs your OK first? (one per line)"),
    ("time_zone", "What time zone are you in? (e.g. Asia/Bangkok)"),
]


@dataclass
class SourceEntry:
    """One row of the source list: App | Account | Allowed | Last test | Limits | Last checked."""
    app: str
    account: str = NOT_KNOWN
    allowed: list[str] = field(default_factory=list)
    state: str = NOT_ENABLED
    last_successful_test: str = ""
    limits: str = ""
    last_checked_through: str = ""


@dataclass
class WorkProfile:
    name: str = NOT_KNOWN
    role: str = NOT_KNOWN
    scope: str = NOT_KNOWN
    goals: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    #: "Name - what they handle"
    people: list[str] = field(default_factory=list)
    writing_style: str = NOT_KNOWN
    #: The person's own messages, kept verbatim, for drafting in their voice.
    style_samples: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    sources: list[SourceEntry] = field(default_factory=list)
    may_do: list[str] = field(default_factory=list)
    needs_ok: list[str] = field(default_factory=list)
    time_zone: str = ""
    open_questions: list[str] = field(default_factory=list)
    last_checked: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkProfile":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        known["sources"] = [SourceEntry(**s) if isinstance(s, dict) else s
                            for s in data.get("sources") or []]
        return cls(**known)

    def source(self, app: str) -> SourceEntry:
        for s in self.sources:
            if s.app == app:
                return s
        entry = SourceEntry(app=app)
        self.sources.append(entry)
        return entry

    def missing(self) -> list[str]:
        """What the person has not told us yet. Shown, never filled in."""
        out = []
        if self.name == NOT_KNOWN:
            out.append("name")
        if self.role == NOT_KNOWN:
            out.append("role")
        if not self.goals:
            out.append("top three goals")
        if not self.people:
            out.append("key people")
        if self.writing_style == NOT_KNOWN and not self.style_samples:
            out.append("writing style")
        if not self.may_do and not self.needs_ok:
            out.append("what needs your OK")
        if not self.time_zone:
            out.append("time zone")
        return out


def path() -> Path:
    return config.TWIN_DIR / "work_profile.json"


def load() -> WorkProfile:
    p = path()
    if not p.exists():
        return WorkProfile()
    return WorkProfile.from_dict(json.loads(p.read_text(encoding="utf-8")))


def save(profile: WorkProfile) -> Path:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    profile.last_checked = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(p)
    LOG.info("profile.saved", missing=profile.missing())
    return p


def record_test(app: str, ok: bool, *, account: str = "", limits: str = "",
                allowed: list[str] | None = None, through: str = "") -> SourceEntry:
    """Record the outcome of a source test. Only a pass makes a source
    "working"; a failure makes it "blocked" and leaves the last successful
    test where it was, so the record says when it last actually worked."""
    profile = load()
    entry = profile.source(app)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if account:
        entry.account = account
    if allowed is not None:
        entry.allowed = list(allowed)
    if limits:
        entry.limits = limits
    if ok:
        entry.state = WORKING
        entry.last_successful_test = now
        entry.last_checked_through = through or now
    else:
        entry.state = BLOCKED
    save(profile)
    return entry


def set_not_enabled(app: str) -> SourceEntry:
    profile = load()
    entry = profile.source(app)
    entry.state = NOT_ENABLED
    save(profile)
    return entry


# -- rendering --------------------------------------------------------------

def _lines(items: list[str], empty: str = NOT_KNOWN) -> list[str]:
    return [f"- {i}" for i in items] if items else [f"- {empty}"]


def render(profile: WorkProfile) -> str:
    out = ["WORK PROFILE", ""]
    out += [f"Name: {profile.name}", f"Role: {profile.role}",
            f"Scope: {profile.scope}", ""]
    out += ["Top three goals:"] + _lines(profile.goals) + [""]
    out += ["Projects and deadlines:"] + _lines(profile.projects, "none tracked") + [""]
    out += ["Key people:"] + _lines(profile.people) + [""]
    out += [f"Writing style: {profile.writing_style}"]
    if profile.style_samples:
        out.append(f"  ({len(profile.style_samples)} sample message(s) on file)")
    out += ["", "Tools and files I may use:"] + _lines(profile.tools) + [""]
    out += ["Source list:",
            "  App | Account/workspace | Allowed sources | State | Last successful test | Limits | Last checked through"]
    if profile.sources:
        for s in profile.sources:
            out.append(f"  {s.app} | {s.account} | {', '.join(s.allowed) or '-'} | {s.state}"
                       f" | {s.last_successful_test or 'never'} | {s.limits or '-'}"
                       f" | {s.last_checked_through or '-'}")
    else:
        out.append("  (no sources tested yet)")
    out += ["", "May do on my own:"] + _lines(profile.may_do, "nothing agreed yet — draft only")
    out += ["Needs your OK:"] + _lines(profile.needs_ok, "everything that leaves this machine")
    out += ["", f"Time zone: {profile.time_zone or NOT_KNOWN}"]
    if profile.open_questions:
        out += ["", "Open questions:"] + _lines(profile.open_questions)
    missing = profile.missing()
    if missing:
        out += ["", "Not known yet: " + ", ".join(missing)]
    out += ["", f"Last checked: {profile.last_checked or 'never'}"]
    return "\n".join(out)


# -- setup --------------------------------------------------------------------

def apply_answer(profile: WorkProfile, key: str, answer: str) -> None:
    """Put one answer into the profile. Empty means skipped: nothing changes."""
    answer = (answer or "").strip()
    if not answer:
        return
    lines = [l.strip() for l in answer.splitlines() if l.strip()]
    if key == "name":
        profile.name = answer
    elif key == "role":
        profile.role = answer
    elif key == "goals":
        profile.goals = lines[:3]
        if len(lines) > 3:
            profile.open_questions.append(
                f"More than three goals given ({len(lines)}); which three come first?")
    elif key == "projects":
        profile.projects = lines
    elif key == "people":
        profile.people = lines
    elif key == "writing_style":
        profile.style_samples.append(answer)
        profile.writing_style = describe_style(profile.style_samples)
    elif key == "tools":
        profile.tools = lines
    elif key == "may_do":
        profile.may_do = lines
    elif key == "needs_ok":
        profile.needs_ok = lines
    elif key == "time_zone":
        profile.time_zone = answer
    else:
        raise KeyError(key)


def describe_style(samples: list[str]) -> str:
    """A plain description of how the person writes, from their own messages.
    Measured, not guessed: sentence length, greetings, sign-offs, casing."""
    text = "\n".join(samples)
    sentences = [s for s in text.replace("\n", " ").split(". ") if s.strip()]
    words = text.split()
    if not words:
        return NOT_KNOWN
    avg = len(words) / max(1, len(sentences))
    traits = []
    traits.append("short sentences" if avg <= 12 else
                  "medium sentences" if avg <= 20 else "long sentences")
    low = text.lower()
    if any(low.startswith(g) for g in ("hi", "hey", "hay", "hello", "morning")):
        traits.append("opens with a greeting")
    else:
        traits.append("no greeting, straight in")
    if any(s in low for s in ("thanks", "cheers", "ta ")):
        traits.append("signs off with thanks")
    if text == text.lower():
        traits.append("all lower case")
    if "!" in text:
        traits.append("uses exclamation marks")
    if "?" in text:
        traits.append("asks direct questions")
    return ", ".join(traits)


def setup(ask=input, say=print) -> WorkProfile:
    """Walk the questions one at a time. `ask`/`say` are injectable so the
    flow is testable and usable from a chat, not only a terminal."""
    profile = load()
    say("Setting up your Work Profile. One question at a time; press Enter to skip.")
    for key, question in QUESTIONS:
        say("")
        say(question)
        if key in ("goals", "projects", "people", "writing_style", "tools",
                   "may_do", "needs_ok"):
            lines = []
            while True:
                line = ask("> ")
                if not line.strip():
                    break
                lines.append(line)
            answer = "\n".join(lines)
        else:
            answer = ask("> ")
        apply_answer(profile, key, answer)
    save(profile)
    say("")
    say(render(profile))
    return profile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Work Profile.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="ask the setup questions one at a time")
    sub.add_parser("show", help="print the profile")
    s = sub.add_parser("set", help="set one field: name, role, scope, time_zone, "
                                   "or a list field with one item per --line")
    s.add_argument("field", choices=[q[0] for q in QUESTIONS] + ["scope"])
    s.add_argument("value", nargs="?", default="")
    s.add_argument("--line", action="append", default=[],
                   help="repeat for list fields")
    q = sub.add_parser("question", help="record an open question for the person")
    q.add_argument("text")
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        setup()
        return 0
    if args.cmd == "show":
        print(render(load()))
        return 0
    if args.cmd == "set":
        profile = load()
        if args.field == "scope":
            profile.scope = args.value or NOT_KNOWN
        else:
            apply_answer(profile, args.field, "\n".join(args.line) or args.value)
        save(profile)
        print(render(profile))
        return 0
    if args.cmd == "question":
        profile = load()
        profile.open_questions.append(args.text)
        save(profile)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
