"""Per-team health checks, for the daily report.

Reads only — logs, launchd, the two ledgers, the corpus, and the
marketing_onsite task store. Never fails the whole check because one team's
data is unreachable; each section reports its own problem instead of aborting
the others, because the report exists precisely to say when something is
broken.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess

from core import config, ledger as ledger_mod, log

LOG = log.get("chief_of_staff")

#: How stale a "heartbeat" logger can be before a team reads as not-running.
#: jira_leader and slack_poller are meant to tick every few minutes; a gap
#: past this means the launchd job died silently, not that it is just quiet.
STALE_AFTER = dt.timedelta(hours=2)

LAUNCHD_JOBS = ("com.pomelo.mcp", "com.pomelo.daily_report")


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _last_event(logger: str) -> dt.datetime | None:
    path = config.LOG_DIR / f"{logger}.jsonl"
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            last = dt.datetime.fromisoformat(record["ts"]).replace(tzinfo=None)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return last


def _heartbeat(logger: str) -> dict:
    last = _last_event(logger)
    if last is None:
        return {"last_seen": None, "stale": True,
                "note": f"logs/{logger}.jsonl has never been written"}
    age = _now() - last
    return {"last_seen": last.isoformat(timespec="minutes"),
            "stale": age > STALE_AFTER,
            "age_minutes": int(age.total_seconds() // 60)}


def launchd() -> dict:
    """Whether the always-on jobs are actually loaded. Never raises — a
    missing `launchctl` (any non-macOS host) reads as unknown, not down."""
    out: dict[str, dict] = {}
    for label in LAUNCHD_JOBS:
        try:
            proc = subprocess.run(["launchctl", "list", label],
                                  capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            out[label] = {"status": "unknown", "detail": str(exc)}
            continue
        if proc.returncode != 0:
            out[label] = {"status": "not loaded"}
            continue
        pid_line = next((l for l in proc.stdout.splitlines() if '"PID"' in l), "")
        last_exit = next((l for l in proc.stdout.splitlines()
                          if '"LastExitStatus"' in l), "")
        out[label] = {
            "status": "running" if "=" in pid_line else "loaded, not running",
            "last_exit_status": last_exit.split("=")[-1].strip("; \n")
                                if last_exit else None,
        }
    return out


def jira_leader() -> dict:
    heartbeat = _heartbeat("jira_leader")
    try:
        with ledger_mod.Ledger() as led:
            stats = led.stats()
            stuck = [r for r in led.by_state() if r["last_error"]]
    except Exception as exc:  # pragma: no cover - db path
        return {"heartbeat": heartbeat, "error": str(exc)[:200]}
    return {"heartbeat": heartbeat, "ledger": stats, "stuck_tickets": len(stuck)}


def slack_leader() -> dict:
    heartbeat = _heartbeat("slack_poller")
    try:
        with ledger_mod.Ledger() as led:
            stats = ledger_mod.SlackLedger(led).stats()
    except Exception as exc:  # pragma: no cover - db path
        return {"heartbeat": heartbeat, "error": str(exc)[:200]}
    return {"heartbeat": heartbeat, "ledger": stats}


def historian() -> dict:
    if not config.CORPUS_DB.exists():
        return {"error": "corpus.db does not exist — run corpus.export / .index"}
    import sqlite3
    try:
        con = sqlite3.connect(config.CORPUS_DB)
        try:
            n = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            by_type = dict(con.execute(
                "SELECT source_type, COUNT(*) FROM documents GROUP BY source_type"))
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"error": str(exc)[:200]}
    mtime = dt.datetime.utcfromtimestamp(config.CORPUS_DB.stat().st_mtime)
    age = _now() - mtime
    return {"documents": n, "by_type": by_type,
            "index_age_hours": round(age.total_seconds() / 3600, 1),
            "stale": age > dt.timedelta(days=2)}


def chief_of_staff() -> dict:
    from core import corrections
    from agents.chief_of_staff import rules as rules_mod
    return {"corrections_logged": len(corrections.load()),
            "rule_proposals_pending": len(rules_mod.cluster())}


def marketing_onsite() -> dict:
    from agents.marketing_onsite import tasks as mo_tasks
    try:
        all_tasks = mo_tasks.all_tasks()
    except Exception as exc:  # pragma: no cover
        return {"error": str(exc)[:200]}
    by_state: dict[str, int] = {}
    for t in all_tasks:
        by_state[t.state] = by_state.get(t.state, 0) + 1
    return {"by_state": by_state, "total": len(all_tasks)}


def check_all() -> dict:
    return {
        "generated": _now().isoformat(timespec="minutes") + "Z",
        "launchd": launchd(),
        "jira_leader": jira_leader(),
        "slack_leader": slack_leader(),
        "historian": historian(),
        "chief_of_staff": chief_of_staff(),
        "marketing_onsite": marketing_onsite(),
    }


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(check_all(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
