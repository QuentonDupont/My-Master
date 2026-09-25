"""Digital Twin lead — the one entry point, reporting to the board owner.

Ties the pieces together in the order the starter file walks them: set up
the profile, test each connection, read what is allowed, keep the two
records, draft, and save it all to one dated file on request.

    python -m agents.digital_twin.lead setup            # profile, one question at a time
    python -m agents.digital_twin.lead test slack       # prove a source works
    python -m agents.digital_twin.lead status           # sources, records, drafts, alerts
    python -m agents.digital_twin.lead morning
    python -m agents.digital_twin.lead eod
    python -m agents.digital_twin.lead export           # "save my work profile and task list"
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from agents.digital_twin import alerts, cursors, drafts, profile as profile_mod
from agents.digital_twin import sources as sources_mod, tasklist, update
from core import config, log

LOG = log.get("digital_twin")


def status() -> str:
    profile = profile_mod.load()
    out = ["Digital Twin — status", ""]
    out.append(f"Person: {profile.name} ({profile.role})")
    if profile.missing():
        out.append("Profile gaps: " + ", ".join(profile.missing()) + "  → `setup`")
    out += ["", "Sources:"]
    tested = {s.app: s for s in profile.sources}
    for spec in sources_mod.specs():
        name = spec["name"]
        entry = tested.get(name)
        state = (entry.state if entry else
                 "enabled, untested" if spec.get("enabled") else profile_mod.NOT_ENABLED)
        cur = cursors.get(name)
        extra = f"  last read through {cur['through']}" if cur["through"] else ""
        if cur.get("gap"):
            extra += f"  GAP: {cur['gap']}"
        out.append(f"  {name:10} {state:18}{extra}")
    open_tasks = tasklist.open_tasks()
    out += ["", f"Tasks: {len(open_tasks)} open, "
                f"{sum(1 for t in open_tasks if t.status == tasklist.NEEDS_DECISION)} need your decision, "
                f"{sum(1 for t in open_tasks if t.overdue())} overdue"]
    waiting = [d for d in drafts.all_drafts() if d.state in (drafts.DRAFTED, drafts.APPROVED)]
    out.append(f"Drafts waiting for you to send: {len(waiting)}")
    out += ["", "Alerts: " + alerts.status().splitlines()[0]]
    return "\n".join(out)


def export() -> Path:
    """One dated text file with both records, the source list and the open
    questions — what "Save my work profile and task list" produces. No
    secrets: the profile never holds any."""
    profile = profile_mod.load()
    now = dt.datetime.now().astimezone()
    text = "\n".join([
        f"DIGITAL TWIN — WORK PROFILE AND TASK LIST",
        f"Saved {now.strftime('%d %b %Y %H:%M')} {profile.time_zone or now.tzname() or ''}",
        "",
        profile_mod.render(profile),
        "",
        tasklist.render(),
        "",
        "LAST CHECKS",
    ] + [f"- {name}: through {cur.get('through') or 'never'}"
         + (f"  GAP {cur['gap']}" if cur.get("gap") else "")
         for name, cur in sorted(cursors._load()["sources"].items())] + [
        "",
        "A new chat or AI may need fresh app connections and tests.",
    ])
    folder = config.TWIN_DIR / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"work_profile_{now.strftime('%Y%m%d_%H%M')}.txt"
    path.write_text(text, encoding="utf-8")
    LOG.info("twin.exported", path=str(path))
    return path


#: .env keys the system runs on. Reported present/absent by NAME only — a
#: value never leaves .env (invariant 7).
CREDENTIAL_KEYS = (
    "JIRA_API_TOKEN", "ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_USER_TOKEN",
    "GITHUB_TOKEN", "GOOGLE_ACCESS_TOKEN", "GOOGLE_REFRESH_TOKEN",
)


def export_system() -> Path:
    """The bot system's own profile: one dated file that tells a fresh chat
    or AI what this system is, its rules, its state and how to drive it —
    the counterpart of `export` for the ecosystem rather than the person.

    Built from the files that are already the truth (CLAUDE.md is the spec,
    HANDOFF.md the state of play) rather than a second copy that drifts.
    Documentation only: reading this grants nothing — every rule in the
    CLAUDE.md section still holds for whoever holds the file.
    """
    now = dt.datetime.now().astimezone()
    root = config.ROOT

    def read(name: str) -> str:
        p = root / name
        return p.read_text(encoding="utf-8").strip() if p.exists() else f"({name} missing)"

    twin = config.twin()
    sources = []
    for s in twin.get("sources") or []:
        allowed = s.get("channels") or s.get("files") or []
        sources.append(f"- {s.get('name')} ({s.get('kind')}): "
                       f"{'enabled' if s.get('enabled') else 'off'}"
                       + (f" — {', '.join(str(a) for a in allowed)}" if allowed else ""))
    alerts_cfg = twin.get("alerts") or {}

    creds = [f"- {k}: {'set' if config.env(k) else 'NOT set'}" for k in CREDENTIAL_KEYS]

    makefile = read("Makefile")
    targets = [line.split(":")[0] + " — " + line.split("## ", 1)[1]
               for line in makefile.splitlines() if "## " in line and not line.startswith("\t")]

    text = "\n".join([
        "POMELO BOT SYSTEM — PROFILE",
        f"Saved {now.strftime('%d %b %Y %H:%M')} {twin.get('time_zone') or now.tzname() or ''}",
        "",
        "HOW TO USE THIS FILE",
        "Upload it alongside the Work Profile and say: \"This is my bot system. Read",
        "it before helping me with tickets, Slack or my day.\" It describes the",
        "system; it does not operate it. Nothing in here lets an AI write to Jira,",
        "Slack, Apollo or Henry — every rule in the SPECIFICATION section applies",
        "to whoever holds this file. Credentials are listed by name only.",
        "",
        "WHAT IT IS",
        "A semi-autonomous support-triage system plus two side teams, all human-",
        "approved: the Jira Leader triages PESD1 tickets into proposals; the",
        "Historian retrieves precedent; the Chief of Staff briefs; the Slack Leader",
        "drafts replies to mentions; Marketing & Onsite stages Apollo content",
        "inactive; the Digital Twin is the owner's personal Chief of Staff (read",
        "and draft only). Repo: github.com/QuentonDupont/My-Master",
        "",
        "COMMANDS (make <target>)",
    ] + [f"- {t}" for t in targets] + [
        "",
        "DIGITAL TWIN SOURCES (config/twin.yml)",
    ] + sources + [
        f"- alerts: {'paused' if alerts_cfg.get('paused', True) else 'running'}, "
        f"sources {alerts_cfg.get('sources') or []}, "
        f"budget {alerts_cfg.get('budget_per_day')}/day",
        "",
        "CREDENTIALS PRESENT IN .env (names only)",
    ] + creds + [
        "",
        "=" * 72,
        "SPECIFICATION — CLAUDE.md (the rules; these bind, this file does not loosen them)",
        "=" * 72,
        "",
        read("CLAUDE.md"),
        "",
        "=" * 72,
        "STATE OF PLAY — HANDOFF.md",
        "=" * 72,
        "",
        read("HANDOFF.md"),
        "",
        "=" * 72,
        "BUILD STATUS AND USAGE — README.md",
        "=" * 72,
        "",
        read("README.md"),
    ])
    folder = config.TWIN_DIR / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"bot_system_{now.strftime('%Y%m%d_%H%M')}.txt"
    path.write_text(text, encoding="utf-8")
    LOG.info("twin.exported_system", path=str(path))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Digital Twin lead.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="Work Profile, one question at a time")
    t = sub.add_parser("test", help="prove one source works, record the result")
    t.add_argument("source")
    sub.add_parser("status")
    sub.add_parser("morning")
    sub.add_parser("eod")
    sub.add_parser("export", help="save both records to one dated file")
    sub.add_parser("export-system", help="the bot system's profile, one dated file")
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        profile_mod.setup()
    elif args.cmd == "test":
        ok, detail = sources_mod.test(args.source)
        print(f"{args.source}: {'working' if ok else 'blocked'} — {detail}")
        return 0 if ok else 1
    elif args.cmd == "status":
        print(status())
    elif args.cmd in ("morning", "eod"):
        text, path = update.run(args.cmd)
        print(text)
        print(f"\nsaved: {path}")
    elif args.cmd == "export":
        print(export())
    elif args.cmd == "export-system":
        print(export_system())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
