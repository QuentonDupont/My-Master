"""The event journal: what happened, in order, for flows triggered On(event).

Ledger state changes write their own events inside core.ledger's transaction
(`ticket.claimed`, `ticket.proposed`, `ticket.approved`, `ticket.executed`, ...).
Anything else that wants to wake a flow — a content task being added, say —
calls `emit()` here.

    python -m core.events recent [--limit 20]
"""
from __future__ import annotations

import argparse
import json

from core import ledger as ledger_mod


def emit(name: str, ticket_key: str | None = None, **data) -> None:
    with ledger_mod.Ledger() as led, led._tx() as conn:
        ledger_mod.emit_in(conn, name, ticket_key, **data)


def _row(r) -> dict:
    d = dict(r)
    d["data"] = json.loads(d["data"]) if d.get("data") else {}
    return d


def latest_id(led: ledger_mod.Ledger | None = None) -> int:
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        row = led.conn.execute("SELECT COALESCE(MAX(id), 0) m FROM events").fetchone()
        return int(row["m"])
    finally:
        if own:
            led.close()


def since(after_id: int, led: ledger_mod.Ledger | None = None) -> list[dict]:
    own = led is None
    led = led or ledger_mod.Ledger()
    try:
        rows = led.conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id", (after_id,)).fetchall()
        return [_row(r) for r in rows]
    finally:
        if own:
            led.close()


def recent(limit: int = 20) -> list[dict]:
    with ledger_mod.Ledger() as led:
        rows = led.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.events")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("recent")
    p.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    for e in reversed(recent(args.limit)):
        print(json.dumps(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
