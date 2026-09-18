"""Slack Leader — the standing agent. Finds mentions, spawns 3 thread workers.

    python -m agents.slack_leader.leader run --file tests/fixtures/mentions.json
    python -m agents.slack_leader.leader run          # live, needs SLACK_BOT_TOKEN
    python -m agents.slack_leader.leader status
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agents.historian.retrieval import Historian
from agents.slack_leader import worker as worker_mod
from core import ledger as ledger_mod, log

LOG = log.get("slack_leader")

MAX_WORKERS = 3


class FileMentionSource:
    """Offline source: a json list of mentions, same shape as the live one."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def mentions(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))


class SlackMentionSource:
    """Live source. Reads threads the bot was mentioned in.

    Slack delivers mentions as events (Socket Mode or an events endpoint); this
    takes the events already collected and fills in each thread's messages.
    """

    def __init__(self, events: list[dict], reader=None) -> None:
        from core.slack_client import SlackReadClient

        self.events = events
        self.reader = reader or SlackReadClient()

    def mentions(self) -> list[dict]:
        out = []
        for event in self.events:
            channel = event.get("channel")
            thread_ts = event.get("thread_ts") or event.get("ts")
            if not channel or not thread_ts:
                continue
            messages = self.reader.thread(channel, thread_ts)
            user_id = event.get("user") or ""
            profile = self.reader.user(user_id) if user_id else {}
            out.append({
                "channel": channel,
                "channel_name": (self.reader.channel(channel) or {}).get("name", channel),
                "thread_ts": thread_ts,
                "user": user_id,
                "user_name": (profile.get("profile") or {}).get("real_name")
                             or profile.get("name") or user_id,
                "permalink": self.reader.permalink(channel, thread_ts),
                "messages": messages,
            })
        return out


def _run_one(mention: dict, bot_user_id: str | None) -> dict:
    with ledger_mod.Ledger() as led, Historian() as hist:
        return worker_mod.process(mention, ledger=led, historian=hist,
                                  bot_user_id=bot_user_id).to_dict()


def run(source, *, max_workers: int = MAX_WORKERS,
        bot_user_id: str | None = None) -> dict:
    mentions = source.mentions()
    claimed, skipped = [], []
    with ledger_mod.Ledger() as led:
        slack_led = ledger_mod.SlackLedger(led)
        for mention in mentions:
            key = slack_led.key(mention["channel"], mention["thread_ts"])
            hash_ = ledger_mod.thread_hash(mention.get("messages") or [])
            ok, reason = slack_led.should_process(key, hash_)
            if not ok:
                skipped.append({"thread": key, "reason": reason})
                continue
            if not slack_led.claim(mention["channel"], mention["thread_ts"], hash_):
                skipped.append({"thread": key, "reason": "claim lost"})
                continue
            claimed.append(mention)

    outcomes = []
    if claimed:
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="slack-worker") as pool:
            futures = {pool.submit(_run_one, m, bot_user_id): m for m in claimed}
            for future in as_completed(futures):
                try:
                    outcomes.append(future.result())
                except Exception as exc:  # pragma: no cover - defensive
                    LOG.error("slack_leader.worker_failed", error=str(exc))
    LOG.info("slack_leader.done", claimed=len(claimed), skipped=len(skipped))
    return {"claimed": len(claimed), "skipped": skipped,
            "outcomes": sorted(outcomes, key=lambda o: o["thread"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.slack_leader.leader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--file", help="offline mentions json")
    p_run.add_argument("--events", help="json file of Slack app_mention events")
    p_run.add_argument("--workers", type=int, default=MAX_WORKERS)
    sub.add_parser("status")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        with ledger_mod.Ledger() as led:
            print(json.dumps(ledger_mod.SlackLedger(led).stats(), indent=2))
        return 0

    if args.file:
        source = FileMentionSource(args.file)
        bot_id = None
    else:
        from core.slack_client import SlackReadClient

        reader = SlackReadClient()
        bot_id = reader.auth_test().get("user_id")
        events = json.loads(Path(args.events).read_text()) if args.events else []
        source = SlackMentionSource(events, reader)
    print(json.dumps(run(source, max_workers=args.workers, bot_user_id=bot_id),
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
