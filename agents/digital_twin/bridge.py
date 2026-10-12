"""The intake queue a live-alert bridge would feed (starter file, implementer
notes 5). The bridge itself — Slack Socket Mode, Gmail Pub/Sub, Drive watch —
is NOT built here; this is the durable, deduplicated queue it hands events to,
so the alert loop has one place to read from whether an event arrived from a
poll or from a push.

Rules, from the notes:
* store minimal event references, never bodies you do not need, never secrets
* deduplicate on exact event ids
* one unfinished batch per destination; acknowledge only after handling
* a provider notice grants no authority to send, spend or change anything

    python -m agents.digital_twin.bridge push '{"source":"slack","id":"C1:1.0","ts":"...","text":"..."}'
    python -m agents.digital_twin.bridge status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from pathlib import Path

from core import config, log

LOG = log.get("digital_twin")

DESTINATION = "owner"   # one person, one intake — never a colleague's

REQUIRED = ("source", "id")
ALLOWED = ("source", "id", "ts", "author", "title", "text", "link", "kind", "meta")


def queue_path() -> Path:
    return config.TWIN_DIR / "bridge_queue.jsonl"


def _state_path() -> Path:
    return config.TWIN_DIR / "bridge_state.json"


def _state() -> dict:
    p = _state_path()
    if not p.exists():
        return {"seen": [], "open_batch": None, "acked": 0}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["seen"] = state["seen"][-5000:]
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def push(event: dict) -> bool:
    """Queue one event reference. Returns False if it was a duplicate.
    Unknown fields are dropped, so a bridge cannot smuggle a body in."""
    missing = [k for k in REQUIRED if not event.get(k)]
    if missing:
        raise ValueError(f"event needs {', '.join(missing)}")
    key = f"{event['source']}:{event['id']}"
    state = _state()
    if key in state["seen"]:
        LOG.info("bridge.duplicate", key=key)
        return False
    ref = {k: event[k] for k in ALLOWED if k in event}
    ref["received"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    p = queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ref, ensure_ascii=False) + "\n")
    state["seen"].append(key)
    _save_state(state)
    LOG.info("bridge.queued", key=key)
    return True


def pending() -> list[dict]:
    p = queue_path()
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def claim(destination: str = DESTINATION) -> tuple[str, list[dict]]:
    """Hand out the unfinished batch if there is one, else everything queued
    as a new batch. Nothing leaves the queue until `ack`."""
    state = _state()
    if state.get("open_batch") and state["open_batch"]["destination"] == destination:
        b = state["open_batch"]
        return b["batch_id"], b["events"]
    events = pending()
    if not events:
        return "", []
    batch_id = "b_" + uuid.uuid4().hex[:6]
    state["open_batch"] = {"batch_id": batch_id, "destination": destination,
                           "events": events,
                           "claimed": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    _save_state(state)
    return batch_id, events


def ack(batch_id: str) -> int:
    """The batch was handled: drop its events from the queue."""
    state = _state()
    b = state.get("open_batch")
    if not b or b["batch_id"] != batch_id:
        raise ValueError(f"no open batch {batch_id}")
    keys = {f"{e['source']}:{e['id']}" for e in b["events"]}
    rest = [e for e in pending() if f"{e['source']}:{e['id']}" not in keys]
    p = queue_path()
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rest),
                 encoding="utf-8")
    state["open_batch"] = None
    state["acked"] = state.get("acked", 0) + len(keys)
    _save_state(state)
    LOG.info("bridge.acked", batch=batch_id, events=len(keys))
    return len(keys)


def status() -> dict:
    state = _state()
    return {"queued": len(pending()), "open_batch": (state.get("open_batch") or {}).get("batch_id"),
            "acked_total": state.get("acked", 0), "seen_ids": len(state["seen"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bridge intake queue.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push")
    p.add_argument("event", help="JSON with at least source and id")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    if args.cmd == "push":
        print("queued" if push(json.loads(args.event)) else "duplicate")
        return 0
    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
