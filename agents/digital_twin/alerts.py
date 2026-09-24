"""Live alerts — the pilot from sections F and G of the starter file.

An alert is raised when a new item in a watched source carries one of the
signals the person asked for: a decision, a promise, a deadline, a blocker or
completed work. Routine items stay quiet. Events arrive two ways and are
handled the same:

* a poll (`once`) reads the sources named under `alerts.sources` in
  config/twin.yml, on their own cursors, separate from the update's;
* a push lands in the bridge queue (`bridge.push`) — that is how a future
  Socket Mode / Pub/Sub bridge, or a `test-event`, gets in.

Paused by default. Budgeted per day. Dedupe on exact ids. Nothing here sends,
replies or changes anything: an alert is a line in twin/alerts.jsonl and on
stdout, for the person to act on.

    python -m agents.digital_twin.alerts status
    python -m agents.digital_twin.alerts resume
    python -m agents.digital_twin.alerts once
    python -m agents.digital_twin.alerts test-event slack "TEST: we agreed to ship Friday"
    python -m agents.digital_twin.alerts pause
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from agents.digital_twin import bridge, cursors, items as items_mod, profile as profile_mod
from agents.digital_twin import sources as sources_mod
from agents.digital_twin.items import Item
from core import config, log

LOG = log.get("digital_twin")

CURSOR_PREFIX = "alerts:"


def settings() -> dict:
    cfg = dict(config.twin().get("alerts") or {})
    cfg.setdefault("paused", True)
    cfg.setdefault("sources", [])
    cfg.setdefault("notify_on", list(items_mod.SIGNALS[:5]))
    cfg.setdefault("budget_per_day", 20)
    return cfg


def _state_path() -> Path:
    return config.TWIN_DIR / "alerts_state.json"


def _state() -> dict:
    p = _state_path()
    if not p.exists():
        return {"paused": None, "counts": {}, "seen": [], "last_run": ""}
    return json.loads(p.read_text(encoding="utf-8"))


def _save(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["seen"] = state["seen"][-5000:]
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def paused() -> bool:
    """The person's last pause/resume wins over the config default."""
    s = _state()
    return settings()["paused"] if s.get("paused") is None else bool(s["paused"])


def pause() -> None:
    s = _state()
    s["paused"] = True
    _save(s)
    LOG.info("alerts.paused")


def resume() -> None:
    s = _state()
    s["paused"] = False
    _save(s)
    LOG.info("alerts.resumed")


def _log_path() -> Path:
    return config.TWIN_DIR / "alerts.jsonl"


def _spent_today(state: dict, today: str) -> int:
    return int(state["counts"].get(today, 0))


def once(clients: dict | None = None, now: dt.datetime | None = None) -> dict:
    """One pass. Returns what was raised, what was quiet, and why it stopped."""
    now = now or dt.datetime.now().astimezone()
    today = now.date().isoformat()
    cfg = settings()
    result = {"alerts": [], "quiet": 0, "stopped": "", "failed": {}, "sources": []}
    if paused():
        result["stopped"] = "paused"
        return result

    state = _state()
    profile = profile_mod.load()
    me = []
    if profile.name != profile_mod.NOT_KNOWN:
        me = [profile.name, profile.name.split()[0]]
    notify_on = set(cfg["notify_on"]) | {items_mod.NEEDS_ME}
    budget = int(cfg["budget_per_day"])

    candidates: list[Item] = []

    # 1. pushed events (bridge / test-event)
    batch_id, events = bridge.claim()
    for e in events:
        candidates.append(Item(source=e["source"], item_id=str(e["id"]),
                               ts=e.get("ts") or now.isoformat(timespec="seconds"),
                               author=e.get("author", ""), title=e.get("title", ""),
                               text=e.get("text", ""), link=e.get("link", ""),
                               kind=e.get("kind", "message"),
                               meta={"via": "bridge", **(e.get("meta") or {})}))

    # 2. polled sources, on the alerts' own cursors
    advance: dict[str, tuple[dt.datetime, list[str]]] = {}
    for name in cfg["sources"]:
        cname = CURSOR_PREFIX + name
        try:
            src = sources_mod.build(name, clients)
            got = src.read(cursors.since(cname, dt.timedelta(hours=1)))
        except sources_mod.SourceError as exc:
            result["failed"][name] = str(exc)
            cursors.mark_failed(cname, str(exc))
            continue
        result["sources"].append(name)
        fresh = set(cursors.unseen(cname, [i.item_id for i in got]))
        candidates += [i for i in got if i.item_id in fresh]
        advance[cname] = (now, [i.item_id for i in got])

    # 3. filter, dedupe, budget
    seen = set(state["seen"])
    for item in candidates:
        if item.key() in seen:
            continue
        seen.add(item.key())
        state["seen"].append(item.key())
        sig = [s for s in items_mod.signals(item, me) if s in notify_on]
        if not sig:
            result["quiet"] += 1
            continue
        if _spent_today(state, today) >= budget:
            result["stopped"] = f"budget spent ({budget}/day)"
            LOG.warn("alerts.budget_spent", budget=budget)
            break
        alert = {"ts": now.isoformat(timespec="seconds"), "source": item.source,
                 "signals": sig, "author": item.author, "title": item.title,
                 "text": item.text[:300], "link": item.link, "id": item.item_id}
        result["alerts"].append(alert)
        state["counts"][today] = _spent_today(state, today) + 1
        with _log_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert, ensure_ascii=False) + "\n")
        LOG.info("alerts.raised", source=item.source, signals=sig, id=item.item_id)

    state["last_run"] = now.isoformat(timespec="seconds")
    _save(state)
    if batch_id:
        bridge.ack(batch_id)
    for cname, (when, ids) in advance.items():
        cursors.advance(cname, when, ids, tz=profile.time_zone)
    return result


def test_event(source: str, text: str, author: str = "test") -> dict:
    """Prove the event path: push a marked test event and run one pass
    without fetching anything by hand (section G, step 2)."""
    marked = text if text.upper().startswith("TEST") else f"TEST: {text}"
    bridge.push({"source": source, "id": f"test:{dt.datetime.now().timestamp():.3f}",
                 "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "author": author, "text": marked, "kind": "message",
                 "meta": {"test": True}})
    return once()


def render(result: dict) -> str:
    out = []
    if result["stopped"] == "paused":
        return "alerts: paused — `resume` to turn the pilot on"
    for a in result["alerts"]:
        who = f"{a['author']}: " if a["author"] else ""
        head = a["title"] or a["text"][:80]
        out.append(f"ALERT [{', '.join(a['signals'])}] {a['source']} — {who}{head}"
                   + (f" {a['link']}" if a["link"] else ""))
    if not result["alerts"]:
        out.append(f"no alerts ({result['quiet']} routine item(s) stayed quiet)")
    if result["failed"]:
        out.append("could not check: " + "; ".join(f"{n} — {e}" for n, e in result["failed"].items()))
    if result["stopped"]:
        out.append(f"stopped: {result['stopped']}")
    return "\n".join(out)


def status() -> str:
    cfg = settings()
    s = _state()
    today = dt.date.today().isoformat()
    lines = [f"pilot: {'paused' if paused() else 'running'}",
             f"sources: {', '.join(cfg['sources']) or '(none — add them under alerts.sources)'}",
             f"notify on: {', '.join(cfg['notify_on'])}",
             f"budget: {_spent_today(s, today)}/{cfg['budget_per_day']} today",
             f"last run: {s.get('last_run') or 'never'}",
             f"queue: {bridge.status()['queued']} pushed event(s) waiting"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live-alert pilot.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("once")
    sub.add_parser("pause")
    sub.add_parser("resume")
    t = sub.add_parser("test-event")
    t.add_argument("source")
    t.add_argument("text")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        print(status())
    elif args.cmd == "once":
        print(render(once()))
    elif args.cmd == "pause":
        pause()
        print(status())
    elif args.cmd == "resume":
        resume()
        print(status())
    elif args.cmd == "test-event":
        print(render(test_event(args.source, args.text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
