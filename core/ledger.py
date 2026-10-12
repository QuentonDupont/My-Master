"""SQLite ledger — the ONLY place ledger state changes.

Invariants enforced here (CLAUDE.md #4, #5):
  * a ticket already in the ledger is re-processed only when its content_hash
    changed AND its Jira status is back to the intake open status;
  * a ticket whose state is EXECUTED is never re-executed.

Every state change goes through `transition()`, which refuses moves that are not
on the state machine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core import config, log

LOG = log.get("ledger")

NEW = "NEW"
CLAIMED = "CLAIMED"
PROPOSED = "PROPOSED"
APPROVED = "APPROVED"
CORRECTED = "CORRECTED"
REJECTED = "REJECTED"
EXECUTED = "EXECUTED"
ESCALATED = "ESCALATED"
DUPLICATE = "DUPLICATE"
ROLLED_BACK = "ROLLED_BACK"

STATES = (NEW, CLAIMED, PROPOSED, APPROVED, CORRECTED, REJECTED, EXECUTED,
          ESCALATED, DUPLICATE, ROLLED_BACK)

#: state -> states it may move to.
TRANSITIONS: dict[str, set[str]] = {
    NEW: {CLAIMED},
    CLAIMED: {PROPOSED, ESCALATED, DUPLICATE, NEW},
    PROPOSED: {APPROVED, CORRECTED, REJECTED, ESCALATED},
    APPROVED: {EXECUTED, REJECTED, ESCALATED},
    CORRECTED: {EXECUTED, REJECTED, ESCALATED},
    REJECTED: {CLAIMED},
    EXECUTED: {ROLLED_BACK},
    ESCALATED: {CLAIMED},
    DUPLICATE: {CLAIMED},
    ROLLED_BACK: {CLAIMED},
}

#: states that mean "this ticket is done unless its content changes".
TERMINAL = {EXECUTED, REJECTED, DUPLICATE, ESCALATED, ROLLED_BACK}

#: A CLAIMED row with no timeout is a silent trap: a worker thread that dies
#: mid-flight (crash, OOM, a killed process) leaves the ticket claimed forever,
#: and should_process() refuses to touch it again — every future sweep skips it
#: with no error anywhere. 30 minutes is generous against a 5-minute sweep
#: cadence; a row past it almost certainly means the worker that claimed it is
#: gone, not still working.
STALE_CLAIM_MINUTES = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  ticket_key      TEXT PRIMARY KEY,
  state           TEXT NOT NULL,
  first_seen      TEXT NOT NULL,
  last_processed  TEXT NOT NULL,
  content_hash    TEXT NOT NULL,
  proposal_id     TEXT,
  clone_key       TEXT,
  comment_id      TEXT,
  attempts        INTEGER DEFAULT 0,
  last_error      TEXT
);
CREATE INDEX IF NOT EXISTS ledger_state_idx ON ledger(state);

-- Append-only journal of every executed/undone write step. Kept out of the
-- ledger table so the schema in CLAUDE.md stays literal.
CREATE TABLE IF NOT EXISTS execution_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  ticket_key   TEXT NOT NULL,
  proposal_id  TEXT,
  step         TEXT NOT NULL,
  ok           INTEGER NOT NULL,
  detail       TEXT
);
CREATE INDEX IF NOT EXISTS execution_log_ticket_idx ON execution_log(ticket_key);
"""

_UPDATABLE = {"content_hash", "proposal_id", "clone_key", "comment_id", "last_error"}


class LedgerError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def content_hash(summary: str, description: str, comment_count: int) -> str:
    payload = f"{summary or ''}{description or ''}{int(comment_count)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or config.LEDGER_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # -- reads -------------------------------------------------------------
    def get(self, ticket_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM ledger WHERE ticket_key = ?", (ticket_key,)
        ).fetchone()
        return dict(row) if row else None

    def by_state(self, *states: str) -> list[dict]:
        if not states:
            rows = self.conn.execute("SELECT * FROM ledger ORDER BY last_processed DESC")
        else:
            marks = ",".join("?" * len(states))
            rows = self.conn.execute(
                f"SELECT * FROM ledger WHERE state IN ({marks}) ORDER BY last_processed DESC",
                states,
            )
        return [dict(r) for r in rows]

    def by_proposal(self, proposal_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM ledger WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) c FROM ledger GROUP BY state")
        return {r["state"]: r["c"] for r in rows}

    # -- stale-claim reclaim -------------------------------------------------
    def reclaim_stale_claims(self, older_than_minutes: int = STALE_CLAIM_MINUTES
                              ) -> list[str]:
        """Move CLAIMED rows past the staleness threshold back to NEW.

        A crashed worker has no other way to release its ticket: should_process()
        blocks CLAIMED unconditionally, so without this a dead worker's claim is
        permanent. Call this once per sweep, before should_process() is consulted
        for anything. Safe to call from multiple processes — each row transitions
        independently and CLAIMED->NEW is already a legal move.
        """
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(minutes=older_than_minutes)).isoformat(
                      timespec="seconds")
        stale = self.conn.execute(
            "SELECT ticket_key, last_processed FROM ledger "
            "WHERE state = ? AND last_processed < ?", (CLAIMED, cutoff)
        ).fetchall()
        reclaimed = []
        for row in stale:
            try:
                self.transition(row["ticket_key"], NEW,
                                last_error=f"reclaimed: CLAIMED since "
                                           f"{row['last_processed']}, worker "
                                           f"presumed dead")
                reclaimed.append(row["ticket_key"])
            except LedgerError:
                continue  # raced with something else; leave it
        if reclaimed:
            LOG.warn("ledger.reclaimed_stale_claims", tickets=reclaimed,
                     older_than_minutes=older_than_minutes)
        return reclaimed

    # -- the re-processing gate (invariant #4) ------------------------------
    def should_process(self, ticket_key: str, hash_: str, status: str) -> tuple[bool, str]:
        """Decide whether a ticket may enter the queue. Pure read, no writes."""
        row = self.get(ticket_key)
        if row is None:
            return True, "new ticket"
        if row["state"] == NEW:
            # Seen but never triaged — e.g. a worker crashed and released it.
            return True, "seen but not yet processed"
        if row["state"] in (CLAIMED, PROPOSED, APPROVED, CORRECTED):
            return False, f"already in flight ({row['state']})"
        open_status = config.boards()["intake"]["open_status"]
        if row["content_hash"] == hash_:
            return False, f"already processed ({row['state']}), content unchanged"
        if status != open_status:
            return False, (
                f"content changed but status is {status!r}, not {open_status!r}"
            )
        return True, f"content changed and status back to {open_status!r}"

    # -- writes ------------------------------------------------------------
    def upsert_new(self, ticket_key: str, hash_: str) -> dict:
        """Register a ticket as NEW (or refresh its hash if it is re-entering)."""
        ts = now()
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO ledger (ticket_key, state, first_seen, last_processed,
                                       content_hash)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(ticket_key) DO UPDATE SET
                     content_hash = excluded.content_hash,
                     last_processed = excluded.last_processed,
                     last_error = NULL""",
                (ticket_key, NEW, ts, ts, hash_),
            )
        LOG.info("ledger.seen", ticket=ticket_key, hash=hash_[:12])
        return self.get(ticket_key)  # type: ignore[return-value]

    def claim(self, ticket_key: str, hash_: str) -> bool:
        """Atomically move a ticket into CLAIMED. False if someone else has it."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT state FROM ledger WHERE ticket_key = ?", (ticket_key,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO ledger (ticket_key, state, first_seen, last_processed,
                                           content_hash, attempts)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (ticket_key, CLAIMED, now(), now(), hash_),
                )
                LOG.info("ledger.claimed", ticket=ticket_key, previous=None)
                return True
            state = row["state"]
            if CLAIMED not in TRANSITIONS.get(state, set()):
                LOG.warn("ledger.claim_refused", ticket=ticket_key, state=state)
                return False
            conn.execute(
                """UPDATE ledger SET state = ?, last_processed = ?, content_hash = ?,
                          attempts = attempts + 1, last_error = NULL
                   WHERE ticket_key = ? AND state = ?""",
                (CLAIMED, now(), hash_, ticket_key, state),
            )
            changed = conn.total_changes
        LOG.info("ledger.claimed", ticket=ticket_key, previous=state)
        return bool(changed)

    def transition(self, ticket_key: str, to_state: str, **fields) -> dict:
        if to_state not in STATES:
            raise LedgerError(f"unknown state {to_state!r}")
        bad = set(fields) - _UPDATABLE
        if bad:
            raise LedgerError(f"fields not writable via transition(): {sorted(bad)}")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM ledger WHERE ticket_key = ?", (ticket_key,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"{ticket_key} is not in the ledger")
            frm = row["state"]
            if to_state not in TRANSITIONS.get(frm, set()):
                raise LedgerError(
                    f"illegal transition {frm} -> {to_state} for {ticket_key}"
                )
            if frm == EXECUTED and to_state == EXECUTED:  # belt and braces
                raise LedgerError(f"{ticket_key} is already EXECUTED")
            sets = ["state = ?", "last_processed = ?"]
            values: list = [to_state, now()]
            for key, value in fields.items():
                sets.append(f"{key} = ?")
                values.append(value)
            values.append(ticket_key)
            conn.execute(f"UPDATE ledger SET {', '.join(sets)} WHERE ticket_key = ?", values)
        LOG.info("ledger.transition", ticket=ticket_key, **{"from": frm, "to": to_state})
        return self.get(ticket_key)  # type: ignore[return-value]

    def note_progress(self, ticket_key: str, **fields) -> None:
        """Record what a partially-completed execution already did.

        Without this a retry after a mid-sequence failure would post a second
        comment: the first one succeeded but nothing remembered it.
        """
        bad = set(fields) - {"comment_id", "clone_key"}
        if bad:
            raise LedgerError(f"note_progress does not write {sorted(bad)}")
        sets = [f"{k} = ?" for k in fields]
        if not sets:
            return
        with self._tx() as conn:
            conn.execute(
                f"UPDATE ledger SET {', '.join(sets)}, last_processed = ? "
                f"WHERE ticket_key = ?",
                (*fields.values(), now(), ticket_key),
            )
        LOG.info("ledger.progress", ticket=ticket_key, **fields)

    def record_error(self, ticket_key: str, error: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE ledger SET last_error = ?, last_processed = ? WHERE ticket_key = ?",
                (str(error)[:2000], now(), ticket_key),
            )
        LOG.error("ledger.error", ticket=ticket_key, error=str(error)[:500])

    def is_executed(self, ticket_key: str) -> bool:
        row = self.get(ticket_key)
        return bool(row and row["state"] == EXECUTED)

    # -- execution journal -------------------------------------------------
    def journal(self, ticket_key: str, proposal_id: str | None, step: str,
                ok: bool, detail: dict | str | None = None) -> None:
        payload = json.dumps(log.redact(detail), default=str) if detail is not None else None
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO execution_log (ts, ticket_key, proposal_id, step, ok, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (now(), ticket_key, proposal_id, step, 1 if ok else 0, payload),
            )

    def journal_for(self, ticket_key: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM execution_log WHERE ticket_key = ? ORDER BY id", (ticket_key,)
        )
        return [dict(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the ledger db")
    sub.add_parser("stats", help="count tickets per state")
    p_show = sub.add_parser("show", help="dump one ticket + its execution journal")
    p_show.add_argument("ticket")
    p_list = sub.add_parser("list", help="list ledger rows, optionally by state")
    p_list.add_argument("states", nargs="*")
    args = ap.parse_args(argv)

    with Ledger() as led:
        if args.cmd == "init":
            print(f"ledger ready at {led.path}")
        elif args.cmd == "stats":
            print(json.dumps(led.stats(), indent=2))
        elif args.cmd == "show":
            print(json.dumps({"ledger": led.get(args.ticket),
                              "journal": led.journal_for(args.ticket)}, indent=2))
        elif args.cmd == "list":
            print(json.dumps(led.by_state(*args.states), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
