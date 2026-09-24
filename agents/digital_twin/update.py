"""Morning and end-of-day updates, and meeting prep.

Starter file, section 4. An update has three parts — what changed, what needs
me, what is next — and one honesty rule: it says what it checked and what it
could not, and never says "no updates" for a source it did not read. The
per-source cursor moves only after a successful read, and only once the
update has been written.

    python -m agents.digital_twin.update morning
    python -m agents.digital_twin.update morning --since 2026-09-23T09:00
    python -m agents.digital_twin.update eod
    python -m agents.digital_twin.update prep "Weekly ops sync"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from agents.digital_twin import cursors, items as items_mod, profile as profile_mod
from agents.digital_twin import sources as sources_mod, tasklist
from agents.digital_twin.items import Item
from core import config, log

LOG = log.get("digital_twin")


def _me(profile) -> list[str]:
    """The person's names and handles, for `needs_me`."""
    out = []
    if profile.name != profile_mod.NOT_KNOWN:
        out.append(profile.name)
        out.append(profile.name.split()[0])
        out.append("@" + profile.name.split()[0].lower())
    return out


def local_now(profile) -> dt.datetime:
    """Now, in the person's time zone when they have given one."""
    now = dt.datetime.now().astimezone()
    if profile.time_zone:
        try:
            from zoneinfo import ZoneInfo
            return now.astimezone(ZoneInfo(profile.time_zone))
        except Exception:
            pass
    return now


def gather(since: dt.datetime | None = None, clients: dict | None = None,
           only: list[str] | None = None, dedupe: bool = True) -> dict:
    """Read every enabled source since its cursor. Returns the new items per
    source, the failures, and the read time per source so the caller can
    advance the cursors once the update is written."""
    read: dict[str, list[Item]] = {}
    failed: dict[str, str] = {}
    through: dict[str, dt.datetime] = {}
    for src in sources_mod.enabled(clients):
        if only and src.name not in only:
            continue
        start = since or cursors.since(src.name)
        now = dt.datetime.now().astimezone()
        try:
            got = src.read(start)
        except sources_mod.SourceError as exc:
            failed[src.name] = str(exc)
            cursors.mark_failed(src.name, str(exc))
            continue
        if dedupe:
            fresh_ids = set(cursors.unseen(src.name, [i.item_id for i in got]))
            got = [i for i in got if i.item_id in fresh_ids]
        read[src.name] = got
        through[src.name] = now
    return {"read": read, "failed": failed, "through": through}


def commit(gathered: dict, tz: str = "") -> None:
    """Advance cursors for the sources that were read. Failed ones keep theirs."""
    for name, when in gathered["through"].items():
        cursors.advance(name, when, [i.item_id for i in gathered["read"][name]], tz=tz)


def classify(gathered: dict, profile) -> dict:
    me = _me(profile)
    changed: list[tuple[Item, list[str]]] = []
    needs_me: list[tuple[Item, list[str]]] = []
    upcoming: list[Item] = []
    routine = 0
    for name, found in gathered["read"].items():
        for item in found:
            sig = items_mod.signals(item, me)
            if item.kind == "event":
                upcoming.append(item)
                continue
            if not sig:
                routine += 1
                continue
            if items_mod.NEEDS_ME in sig or name == "triage":
                needs_me.append((item, sig))
            else:
                changed.append((item, sig))
    upcoming.sort(key=lambda i: i.ts)
    return {"changed": changed, "needs_me": needs_me, "upcoming": upcoming,
            "routine": routine}


def _line(item: Item, sig: list[str]) -> str:
    who = f"{item.author}: " if item.author else ""
    head = item.title or item.text[:60]
    body = item.text.strip().replace("\n", " ")
    if item.title and body:
        head += f" — {body[:140]}"
    tags = f" [{', '.join(sig)}]" if sig else ""
    link = f" {item.link}" if item.link else ""
    return f"- {who}{head}{tags}{link}"


def render(kind: str, gathered: dict, groups: dict, profile,
           now: dt.datetime | None = None) -> str:
    now = now or local_now(profile)
    tz = profile.time_zone or now.tzname() or ""
    title = {"morning": "Morning update", "eod": "End-of-day update"}.get(kind, "Update")
    out = [f"{title} — {now.strftime('%a %d %b %Y %H:%M')} {tz}", ""]

    checked = sorted(gathered["read"])
    failed = gathered["failed"]
    out.append("Checked: " + (", ".join(
        f"{n} ({len(gathered['read'][n])} new)" for n in checked) or "nothing"))
    if failed:
        out.append("Could not check: " + "; ".join(
            f"{n} — {why}" for n, why in failed.items()))
        for n in failed:
            cur = cursors.get(n)
            if cur["through"]:
                out.append(f"  {n} last read through {cur['through']}")
    not_enabled = [s["name"] for s in sources_mod.specs() if not s.get("enabled")]
    if not_enabled:
        out.append("Not enabled: " + ", ".join(not_enabled))
    out.append("")

    if kind == "eod":
        done_today = [t for t in tasklist.all_tasks()
                      if t.status == tasklist.DONE and t.updated[:10] == now.date().isoformat()]
        out += ["WHAT GOT DONE"] + ([f"- {t.task} — proof: {t.proof}" for t in done_today]
                                   or ["- nothing marked done today (Done needs proof)"]) + [""]

    out.append("WHAT CHANGED")
    if groups["changed"]:
        out += [_line(i, s) for i, s in groups["changed"]]
    elif checked:
        out.append("- nothing that reads as a decision, promise, deadline, blocker "
                   "or completion" + (f" ({groups['routine']} routine items skipped)"
                                      if groups["routine"] else ""))
    else:
        out.append("- not checked — see above")
    out.append("")

    out.append("WHAT NEEDS ME")
    if groups["needs_me"]:
        out += [_line(i, s) for i, s in groups["needs_me"]]
    decisions = [t for t in tasklist.open_tasks() if t.status == tasklist.NEEDS_DECISION]
    for t in decisions:
        out.append(f"- task {t.task_id}: {t.task} — {t.next_step or 'decide'}")
    if not groups["needs_me"] and not decisions:
        out.append("- nothing found" if checked else "- unknown — sources not checked")
    out.append("")

    out.append("WHAT IS NEXT" if kind != "eod" else "TOMORROW")
    today = now.date()
    stuck = [t for t in tasklist.open_tasks() if t.status == tasklist.WAITING]
    if kind == "eod" and stuck:
        out += [f"- waiting on {t.owner}: {t.task}" for t in stuck]
    horizon = today + dt.timedelta(days=7)
    for item in groups["upcoming"]:
        when = item.when
        if when and today <= when.date() <= horizon:
            label = ("today" if when.date() == today else
                     "tomorrow" if when.date() == today + dt.timedelta(days=1) else
                     when.strftime("%a"))
            out.append(f"- {label} {when.strftime('%H:%M')}: {item.title}"
                       + (f" {item.link}" if item.link else ""))
    due = [t for t in tasklist.open_tasks() if t.due]
    for t in sorted(due, key=lambda t: t.due):
        try:
            d = dt.date.fromisoformat(t.due)
        except ValueError:
            continue
        if d <= horizon:
            flag = "OVERDUE" if d < today else "due today" if d == today else f"due {d.strftime('%a %d')}"
            out.append(f"- {flag}: {t.task} ({t.due_kind}, {t.owner})")
    if out[-1] in ("WHAT IS NEXT", "TOMORROW"):
        out.append("- nothing on the calendar or task list this week"
                   if "calendar" in checked else
                   "- no calendar checked; nothing due on the task list this week")
    out.append("")
    if profile.missing():
        out.append("Profile gaps: " + ", ".join(profile.missing()))
    return "\n".join(out)


def run(kind: str = "morning", since: dt.datetime | None = None,
        clients: dict | None = None, commit_cursors: bool = True) -> tuple[str, Path]:
    profile = profile_mod.load()
    gathered = gather(since, clients)
    groups = classify(gathered, profile)
    text = render(kind, gathered, groups, profile)
    path = _write(kind, text, gathered)
    if commit_cursors:
        commit(gathered, profile.time_zone)
    LOG.info("update.written", kind=kind, path=str(path),
             checked=sorted(gathered["read"]), failed=sorted(gathered["failed"]))
    return text, path


def _write(kind: str, text: str, gathered: dict) -> Path:
    folder = config.TWIN_DIR / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"{kind}_{stamp}.md"
    path.write_text(text, encoding="utf-8")
    (folder / f"{kind}_{stamp}.json").write_text(json.dumps({
        "read": {n: [i.to_dict() for i in v] for n, v in gathered["read"].items()},
        "failed": gathered["failed"]}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def prep(subject: str, clients: dict | None = None) -> str:
    """Meeting prep: purpose, key facts, open questions, decisions needed —
    from what the sources say about the subject and what the task list owes."""
    profile = profile_mod.load()
    gathered = gather(dt.datetime.now().astimezone() - dt.timedelta(days=14), clients,
                      dedupe=False)
    words = [w.lower() for w in subject.split() if len(w) > 2]
    related = [i for found in gathered["read"].values() for i in found
               if any(w in f"{i.title} {i.text}".lower() for w in words)]
    tasks = [t for t in tasklist.open_tasks()
             if any(w in f"{t.task} {t.next_step}".lower() for w in words)]
    out = [f"Meeting prep — {subject}", "",
           "Purpose: (not stated — say what this meeting is for)", "",
           "Key facts:"]
    out += [_line(i, items_mod.signals(i, _me(profile))) for i in related[:10]] or ["- nothing found in the sources"]
    out += ["", "Open questions:"]
    asks = [i for i in related if "?" in i.text]
    out += [f"- {i.author}: {i.text.strip()[:140]}" for i in asks[:6]] or ["- none found"]
    out += ["", "Decisions needed:"]
    out += [f"- {t.task} ({t.status}, {t.owner})" for t in tasks] or ["- none on the task list"]
    if gathered["failed"]:
        out += ["", "Could not check: " + ", ".join(gathered["failed"])]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Digital Twin updates.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for kind in ("morning", "eod"):
        p = sub.add_parser(kind)
        p.add_argument("--since", default="", help="ISO datetime; default: since last update")
        p.add_argument("--no-commit", action="store_true", help="leave cursors alone")
    p = sub.add_parser("prep")
    p.add_argument("subject")
    args = ap.parse_args(argv)

    if args.cmd in ("morning", "eod"):
        since = dt.datetime.fromisoformat(args.since).astimezone() if args.since else None
        text, path = run(args.cmd, since, commit_cursors=not args.no_commit)
        print(text)
        print(f"\nsaved: {path}")
        return 0
    if args.cmd == "prep":
        print(prep(args.subject))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
