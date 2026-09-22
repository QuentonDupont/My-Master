"""Marketing & Onsite lead. The only member of this team that reports to Quenton.

Quenton's instruction on 21 Sep 2026: *"make sure they report to me directly"*.
So this team does not route through the Jira Leader or the Chief of Staff. Work
arrives from him, three workers draft it concurrently, and the lead hands him
one report.

    python -m agents.marketing_onsite.lead add "pull the partner banner off HK" \
        --surface web_hero --markets TH
    python -m agents.marketing_onsite.lead run          # draft everything new
    python -m agents.marketing_onsite.lead report       # what he needs to see
"""
from __future__ import annotations

import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from core import log
from agents.marketing_onsite import sops, surfaces, tasks, worker

LOG = log.get("marketing_onsite")

WORKERS = ("w1", "w2", "w3")


def add(request: str, *, surface: str = "", markets=None, assets=None,
        copy_text: str = "", link: str = "") -> tasks.ContentTask:
    """Take in one piece of work from Quenton."""
    task = tasks.from_request(
        request, surface=surface, markets=markets or [], assets=assets or [],
        copy_text=copy_text, link=link,
        created=dt.datetime.now().astimezone().isoformat(timespec="seconds"))

    if not task.surface:
        guesses = sops.search(request, limit=3)
        if guesses:
            task.notes = ("Surface not stated. Closest SOPs by wording: "
                          + "; ".join(g.ref for g in guesses)
                          + ". Name the surface explicitly before drafting.")
    if not task.markets:
        hinted = tasks.sniff_markets(request)
        if hinted:
            task.notes = (task.notes + "\n" if task.notes else "") + (
                f"Markets mentioned in the request: {', '.join(hinted)}. "
                f"Not applied — confirm them, because an assumed market list "
                f"is how a TH-only offer reaches HK.")
    tasks.save(task)
    LOG.info("content.intake", task=task.task_id, surface=task.surface or "?")
    return task


def run(limit: int = 12) -> list[tasks.ContentTask]:
    """Draft everything waiting, three at a time."""
    queue = tasks.in_state(tasks.NEW)[:limit]
    if not queue:
        return []
    for t in queue:
        t.state = tasks.CLAIMED
        tasks.save(t)

    def one(pair):
        idx, task = pair
        return worker.draft(task, worker=WORKERS[idx % len(WORKERS)])

    with ThreadPoolExecutor(max_workers=len(WORKERS)) as pool:
        list(pool.map(one, enumerate(queue)))

    done = [tasks.load(t.task_id) for t in queue]
    LOG.info("content.run", drafted=sum(1 for t in done
                                        if t.state == tasks.DRAFTED),
             needs_input=sum(1 for t in done if t.state == tasks.NEEDS_INPUT))
    return done


def report() -> str:
    """One report for Quenton. Live-publishing changes first, because that is
    the thing he must see before anything else."""
    drafted = tasks.in_state(tasks.DRAFTED)
    blocked = tasks.in_state(tasks.NEEDS_INPUT)

    plans = [p for p in (worker.load_plan(t.task_id) for t in drafted) if p]
    live = [p for p in plans if p.stages_live]
    staged = [p for p in plans if not p.stages_live]

    out: list[str] = ["Marketing & Onsite — content queue", ""]

    if live:
        out.append(f"PUBLISHES ON SAVE ({len(live)}) — no way to stage these:")
        for p in live:
            out.append(f"  {p.task_id}  {p.surface_label}")
            out.append(f"      {p.path}")
            out.append(f"      markets: {', '.join(p.markets) or 'TBC'}")
        out.append("")

    if staged:
        out.append(f"Staged, nothing visible to customers ({len(staged)}):")
        for p in staged:
            out.append(f"  {p.task_id}  {p.surface_label}  [{p.staging}]")
            out.append(f"      {p.path}")
            out.append(f"      markets on approval: "
                       f"{', '.join(p.markets) or 'TBC'}")
            if p.warnings:
                out.append(f"      note: {p.warnings[0]}")
        out.append("")

    if blocked:
        out.append(f"Waiting on you ({len(blocked)}):")
        for t in blocked:
            out.append(f"  {t.task_id}  {t.request[:70]}")
            for line in (t.notes or "").splitlines():
                if line.strip():
                    out.append(f"      {line.strip()}")
        out.append("")

    if not (live or staged or blocked):
        out.append("Nothing waiting.")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Marketing & Onsite lead.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="take in a task from Quenton")
    a.add_argument("request")
    a.add_argument("--surface", default="",
                   help="one of: " + ", ".join(sorted(surfaces.SURFACES)))
    a.add_argument("--markets", default="",
                   help="comma separated, e.g. TH,SG")
    a.add_argument("--assets", default="", help="comma separated files/folders")
    a.add_argument("--copy", dest="copy_text", default="")
    a.add_argument("--link", default="")

    sub.add_parser("run", help="draft everything waiting")
    sub.add_parser("report", help="the report for Quenton")
    sub.add_parser("surfaces", help="what this team may touch")

    args = ap.parse_args(argv)

    if args.cmd == "add":
        markets = [m for m in args.markets.split(",") if m.strip()]
        assets = [a for a in args.assets.split(",") if a.strip()]
        t = add(args.request, surface=args.surface, markets=markets,
                assets=assets, copy_text=args.copy_text, link=args.link)
        print(f"{t.task_id}  {t.state}")
        for gap in t.blocking_gaps():
            print(f"  needs: {gap}")
        return 0

    if args.cmd == "run":
        done = run()
        print(f"{len(done)} task(s) processed")
        print(report())
        return 0

    if args.cmd == "report":
        print(report())
        return 0

    if args.cmd == "surfaces":
        for s in surfaces.SURFACES.values():
            flag = "LIVE ON SAVE" if s.stages_live else s.staging
            print(f"{s.key:22} {flag:12} {s.platform:5} {s.path}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
