"""The scheduler: one tick decides which flows are due and starts them.

launchd runs `python -m flows tick` every minute (tools/com.pomelo.flows.plist).
A tick is cheap — it reads trigger state, starts each due flow in its own
detached process, and exits. A slow flow (the corpus export) therefore never
delays a fast one (the intake sweep).

State lives in the ledger database, beside the event journal:

    flow_state   one row per flow: the event cursor and the run lock
    flow_runs    one row per run: trigger, start, finish, ok, step summary

Rules:
  * Every(n)   due when the last run started n seconds ago or more.
  * Daily(at)  due once per slot: the slot has passed today and no run has
               started since it.
  * On(event)  due when an event past the flow's cursor matches. A flow's
               cursor starts at the journal's current end, so installing the
               scheduler never replays history.
  * Human()    never due. It runs only from `flows run <name> --confirm`
               (or the MCP tool that wraps it).

Scheduled and event runs are dry-run unless the flow is `live_when_scheduled`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from typing import Callable

from core import alerts, config, events, ledger as ledger_mod, log
from core.flow import Context, Daily, Every, Flow, FlowError, Human, On, RunResult

LOG = log.get("scheduler")

EVENT_RETRY_SECONDS = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_state (
  flow        TEXT PRIMARY KEY,
  cursor      INTEGER NOT NULL DEFAULT 0,
  locked_by   TEXT,
  locked_at   TEXT
);
CREATE TABLE IF NOT EXISTS flow_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  flow        TEXT NOT NULL,
  trigger     TEXT NOT NULL,
  started     TEXT NOT NULL,
  finished    TEXT,
  ok          INTEGER,
  summary     TEXT
);
CREATE INDEX IF NOT EXISTS flow_runs_flow_idx ON flow_runs(flow, started);
"""


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


class Store:
    """Scheduler state, in the ledger database."""

    def __init__(self, led: ledger_mod.Ledger | None = None) -> None:
        self._own = led is None
        self.led = led or ledger_mod.Ledger()
        self.conn = self.led.conn
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        if self._own:
            self.led.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- state rows ----------------------------------------------------------
    def state(self, flow: str) -> dict:
        row = self.conn.execute("SELECT * FROM flow_state WHERE flow = ?", (flow,)).fetchone()
        if row is None:
            # A new flow starts at the journal's end: no replay of history.
            self.conn.execute(
                "INSERT OR IGNORE INTO flow_state (flow, cursor) VALUES (?, ?)",
                (flow, events.latest_id(self.led)))
            row = self.conn.execute("SELECT * FROM flow_state WHERE flow = ?",
                                    (flow,)).fetchone()
        return dict(row)

    def advance(self, flow: str, cursor: int) -> None:
        self.conn.execute("UPDATE flow_state SET cursor = MAX(cursor, ?) WHERE flow = ?",
                          (cursor, flow))

    def acquire(self, flow: Flow, owner: str) -> bool:
        self.state(flow.name)
        cutoff = _iso(_utc() - dt.timedelta(seconds=flow.timeout_seconds))
        with self.led._tx() as conn:
            cur = conn.execute(
                "UPDATE flow_state SET locked_by = ?, locked_at = ? WHERE flow = ? "
                "AND (locked_by IS NULL OR locked_at < ?)",
                (owner, _iso(_utc()), flow.name, cutoff))
            return cur.rowcount == 1

    def release(self, flow: str, owner: str) -> None:
        self.conn.execute(
            "UPDATE flow_state SET locked_by = NULL, locked_at = NULL "
            "WHERE flow = ? AND locked_by = ?", (flow, owner))

    def locked(self, flow: Flow) -> bool:
        st = self.state(flow.name)
        if not st["locked_by"]:
            return False
        age = (_utc() - _parse(st["locked_at"])).total_seconds()
        return age < flow.timeout_seconds

    # -- run history -----------------------------------------------------------
    def start_run(self, flow: str, trigger: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO flow_runs (flow, trigger, started) VALUES (?, ?, ?)",
            (flow, trigger, _iso(_utc())))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, result: RunResult) -> None:
        self.conn.execute(
            "UPDATE flow_runs SET finished = ?, ok = ?, summary = ? WHERE id = ?",
            (_iso(_utc()), int(result.ok), json.dumps(result.summary(), default=str),
             run_id))

    def last_run(self, flow: str, *, ok: bool | None = None) -> dict | None:
        sql = "SELECT * FROM flow_runs WHERE flow = ?"
        args: list = [flow]
        if ok is not None:
            sql += " AND ok = ?"
            args.append(int(ok))
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def runs(self, flow: str | None = None, limit: int = 20) -> list[dict]:
        if flow:
            rows = self.conn.execute(
                "SELECT * FROM flow_runs WHERE flow = ? ORDER BY id DESC LIMIT ?",
                (flow, limit))
        else:
            rows = self.conn.execute("SELECT * FROM flow_runs ORDER BY id DESC LIMIT ?",
                                     (limit,))
        return [dict(r) for r in rows]


# -- deciding what is due ----------------------------------------------------
def pending_events(flow: Flow, store: Store) -> list[dict]:
    wanted = [t for t in flow.triggers if isinstance(t, On)]
    if not wanted:
        return []
    cursor = store.state(flow.name)["cursor"]
    return [e for e in events.since(cursor, store.led)
            if any(t.matches(e["name"]) for t in wanted)]


def due(flow: Flow, store: Store, now: dt.datetime | None = None) -> str | None:
    """Why this flow should start now, or None. `now` is timezone-aware."""
    now = now or _utc()
    last = store.last_run(flow.name)
    last_started = _parse(last["started"]) if last else None
    for t in flow.triggers:
        if isinstance(t, Every):
            if last_started is None or (now - last_started).total_seconds() >= t.seconds:
                return "schedule"
        elif isinstance(t, Daily):
            slot = t.slot(now.astimezone())
            if slot is not None and (last_started is None or last_started < slot):
                return "schedule"
    if pending_events(flow, store):
        # A failed run leaves its events unconsumed so they are retried — but
        # not every minute: back off for EVENT_RETRY_SECONDS after a failure.
        if (last and last["ok"] == 0 and last_started is not None
                and (now - last_started).total_seconds() < EVENT_RETRY_SECONDS):
            return None
        return "event"
    return None


# -- running -------------------------------------------------------------------
def run_flow(flow: Flow, *, trigger: str = "manual", execute: bool = False,
             confirm: bool = False, notify: bool = False,
             data: dict | None = None) -> RunResult:
    """Run one flow under its lock, record it, and consume its pending events."""
    human_only = bool(flow.triggers) and all(isinstance(t, Human) for t in flow.triggers)
    if human_only and not confirm:
        raise FlowError(f"{flow.name} runs only on a human confirm (--confirm)")
    if trigger in ("schedule", "event"):
        if human_only:
            raise FlowError(f"{flow.name} cannot be scheduled")
        execute = flow.live_when_scheduled
    owner = f"{os.getpid()}@{_iso(_utc())}"
    with Store() as store:
        if not store.acquire(flow, owner):
            LOG.info("scheduler.skip_locked", flow=flow.name)
            return RunResult(flow.name, True, trigger, stopped="already running")
        pending = pending_events(flow, store)
        run_id = store.start_run(flow.name, trigger)
        try:
            ctx = Context(flow.name, trigger=trigger, execute=execute,
                          data=data, events=pending)
            result = flow.run(ctx)
        except Exception as exc:  # the runtime itself failing still gets recorded
            result = RunResult(flow.name, False, trigger, error=str(exc)[:500])
        store.finish_run(run_id, result)
        if pending and result.ok:
            store.advance(flow.name, max(e["id"] for e in pending))
        store.release(flow.name, owner)
    if notify:
        marker = f"flow_{flow.name}"
        if result.ok:
            alerts.clear(marker)
            alerts.heartbeat(f"flow_{flow.name}")
        else:
            alerts.alert_once(marker, f"My-Master: {flow.name} flow failing",
                              (result.error or "; ".join(result.errors))[:200])
    return result


def spawn(flow: Flow, trigger: str) -> None:
    """Start one flow in its own detached process, output to logs/flows/."""
    out_dir = config.LOG_DIR / "flows"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{flow.name}.out").open("a") as out:
        subprocess.Popen(
            [sys.executable, "-m", "flows", "run", flow.name, "--trigger", trigger,
             "--from-tick"],
            cwd=str(config.ROOT), stdout=out, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)


def tick(registry: dict[str, Flow], *, launcher: Callable[[Flow, str], None] = spawn,
         now: dt.datetime | None = None) -> list[dict]:
    """Start every due flow that is not already running. Returns what started."""
    started: list[dict] = []
    with Store() as store:
        for flow in registry.values():
            if not flow.triggers or all(isinstance(t, Human) for t in flow.triggers):
                continue
            why = due(flow, store, now)
            if why is None:
                continue
            if store.locked(flow):
                LOG.info("scheduler.still_running", flow=flow.name)
                continue
            started.append({"flow": flow.name, "trigger": why})
    for s in started:
        try:
            launcher(registry[s["flow"]], s["trigger"])
        except Exception as exc:
            LOG.error("scheduler.launch_failed", flow=s["flow"], error=str(exc)[:500])
            s["error"] = str(exc)[:200]
    LOG.info("scheduler.tick", started=[s["flow"] for s in started])
    return started


def status(registry: dict[str, Flow]) -> list[dict]:
    out = []
    with Store() as store:
        for flow in registry.values():
            last = store.last_run(flow.name)
            last_ok = store.last_run(flow.name, ok=True)
            out.append({
                "flow": flow.name,
                "triggers": [t.describe() for t in flow.triggers] or ["sub-flow"],
                "running": store.locked(flow),
                "last_run": last and {"started": last["started"], "ok": bool(last["ok"])
                                      if last["ok"] is not None else None,
                                      "trigger": last["trigger"]},
                "last_ok": last_ok and last_ok["started"],
                "pending_events": len(pending_events(flow, store)),
            })
    return out
