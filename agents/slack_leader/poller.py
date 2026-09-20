"""Slack poller — makes the Slack Leader react instead of waiting to be called.

    python -m agents.slack_leader.poller once           # live, needs SLACK_BOT_TOKEN
    python -m agents.slack_leader.poller once --history tests/fixtures/slack_history.json
    python -m agents.slack_leader.poller state          # show the per-channel cursor
    python -m agents.slack_leader.poller reset          # forget the cursor

Slack delivers mentions as `app_mention` events over Socket Mode or an events
endpoint. Neither is running here and neither can be, unattended, without a public
endpoint or a long-lived socket. So this walks `conversations.history` for the
channels the bot is in, finds messages that mention it, and synthesises the same
event shape `SlackMentionSource` already consumes. The leader is unchanged.

It only reads. A mention becomes a *proposal*, exactly as it does when the leader
is run by hand — the human still approves every reply (invariant: every outbound
message is approved by a human first). The poller has no write client and no path
to one.

Dedupe is belt and braces: the per-channel cursor stops the same message being
read twice, and `SlackLedger.should_process` stops a thread being worked twice
even if the cursor is lost or rewound.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agents.slack_leader import leader as leader_mod
from core import config, log

LOG = log.get("slack_poller")

#: how far back to look the first time a channel is seen (seconds)
FIRST_LOOKBACK_S = 24 * 60 * 60

#: messages read per channel per poll
PAGE_LIMIT = 50


def cursor_path() -> Path:
    """Resolved per call so the test sandbox's REVIEW_DIR is honoured."""
    return config.REVIEW_DIR / "slack_poll_cursor.json"


def load_cursors() -> dict[str, str]:
    path = cursor_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        LOG.warn("slack_poller.cursor_unreadable", path=str(path))
        return {}
    return {k: str(v) for k, v in (data.get("channels") or {}).items()}


def save_cursors(cursors: dict[str, str]) -> None:
    path = cursor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "channels": cursors}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


class FileSlackReader:
    """Offline stand-in for SlackReadClient, backed by a fixture file.

    Implements exactly the read surface the poller and SlackMentionSource use, so
    a fixture run exercises the real code path rather than a shortcut around it.
    """

    def __init__(self, path: Path | str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.bot_user_id: str = data.get("bot_user_id", "UBOT")
        self._channels: list[dict] = data.get("channels") or []
        self._messages: dict[str, list[dict]] = data.get("messages") or {}
        self._users: dict[str, dict] = data.get("users") or {}

    def auth_test(self) -> dict:
        return {"user_id": self.bot_user_id, "user": "triage-bot"}

    def conversations(self) -> list[dict]:
        return list(self._channels)

    def history(self, channel: str, oldest: str | None = None,
                limit: int = PAGE_LIMIT) -> list[dict]:
        msgs = sorted(self._messages.get(channel, []),
                      key=lambda m: float(m.get("ts", 0)))
        if oldest:
            msgs = [m for m in msgs if float(m.get("ts", 0)) > float(oldest)]
        return msgs[-limit:]

    def thread(self, channel: str, thread_ts: str, limit: int = 100) -> list[dict]:
        return [m for m in self._messages.get(channel, [])
                if (m.get("thread_ts") or m.get("ts")) == thread_ts]

    def user(self, user_id: str) -> dict:
        return self._users.get(user_id, {"name": user_id})

    def channel(self, channel_id: str) -> dict:
        for chan in self._channels:
            if chan.get("id") == channel_id:
                return chan
        return {"id": channel_id, "name": channel_id}

    def permalink(self, channel: str, ts: str) -> str:
        return f"https://pomelo.slack.com/archives/{channel}/p{ts.replace('.', '')}"


def _is_mention(text: str, bot_user_id: str) -> bool:
    return bool(bot_user_id) and f"<@{bot_user_id}>" in (text or "")


def _from_bot(message: dict, bot_user_id: str) -> bool:
    return message.get("user") == bot_user_id or bool(message.get("bot_id"))


def resolve_channels(reader, channels: list[str] | None) -> list[dict]:
    """Which channels to sweep.

    An explicit list keeps the Slack app down to `channels:history` —
    asking it to discover its own channels needs `channels:read`/`groups:read`
    on top, which is a wider grant than watching a named few.
    """
    if channels:
        return [{"id": c.strip()} for c in channels if c and c.strip()]
    return reader.conversations()


def discover(reader, cursors: dict[str, str], bot_user_id: str, *,
             first_lookback_s: int = FIRST_LOOKBACK_S,
             channels: list[str] | None = None,
             now: float | None = None) -> tuple[list[dict], dict[str, str]]:
    """Read each channel forward from its cursor; return app_mention-shaped events.

    Returns (events, updated_cursors). The cursor advances over every message
    read, mention or not, so a busy channel is not re-scanned.
    """
    now = time.time() if now is None else now
    events: list[dict] = []
    updated = dict(cursors)

    for chan in resolve_channels(reader, channels):
        channel_id = chan.get("id")
        if not channel_id:
            continue
        oldest = cursors.get(channel_id) or f"{now - first_lookback_s:.6f}"
        try:
            messages = reader.history(channel_id, oldest=oldest, limit=PAGE_LIMIT)
        except Exception as exc:  # one bad channel must not stop the sweep
            LOG.warn("slack_poller.history_failed",
                        channel=channel_id, error=str(exc))
            continue

        high_water = oldest
        for message in messages:
            ts = str(message.get("ts") or "")
            if not ts:
                continue
            if float(ts) > float(high_water):
                high_water = ts
            if _from_bot(message, bot_user_id):
                continue
            if not _is_mention(message.get("text", ""), bot_user_id):
                continue
            events.append({
                "channel": channel_id,
                "user": message.get("user", ""),
                "ts": ts,
                "thread_ts": message.get("thread_ts") or ts,
                "text": message.get("text", ""),
            })
        updated[channel_id] = high_water

    return events, updated


def poll_once(reader=None, bot_user_id: str | None = None, *,
              commit_cursor: bool = True, max_workers: int | None = None,
              first_lookback_s: int = FIRST_LOOKBACK_S,
              channels: list[str] | None = None) -> dict:
    """One sweep: discover mentions, hand them to the leader, advance the cursor."""
    if reader is None:
        from core.slack_client import SlackReadClient

        reader = SlackReadClient()
    if bot_user_id is None:
        bot_user_id = reader.auth_test().get("user_id")

    cursors = load_cursors()
    events, updated = discover(reader, cursors, bot_user_id,
                               first_lookback_s=first_lookback_s,
                               channels=channels)

    summary: dict = {"events": len(events), "claimed": 0, "skipped": []}
    if events:
        source = leader_mod.SlackMentionSource(events, reader)
        kwargs = {"bot_user_id": bot_user_id}
        if max_workers is not None:
            kwargs["max_workers"] = max_workers
        result = leader_mod.run(source, **kwargs)
        summary.update(result)

    # Only commit once the work landed: a crash mid-run re-reads those messages,
    # and the ledger stops them being worked twice.
    if commit_cursor:
        save_cursors(updated)
    summary["cursors"] = updated
    LOG.info("slack_poller.done", events=len(events),
             claimed=summary.get("claimed", 0), channels=len(updated))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.slack_leader.poller")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_once = sub.add_parser("once", help="one sweep")
    p_once.add_argument("--history", help="offline history fixture json")
    p_once.add_argument("--workers", type=int, default=None)
    p_once.add_argument("--no-commit", action="store_true",
                        help="do not advance the stored cursor")
    p_once.add_argument("--channels",
                        help="comma-separated channel ids to sweep. Without it "
                             "the bot lists its own channels, which needs the "
                             "channels:read / groups:read scopes as well")
    p_once.add_argument("--lookback", type=int, default=FIRST_LOOKBACK_S,
                        help="seconds of history to read the first time a "
                             f"channel is seen (default {FIRST_LOOKBACK_S})")
    sub.add_parser("state", help="print the stored cursor")
    sub.add_parser("reset", help="forget the stored cursor")

    args = ap.parse_args(argv)

    if args.cmd == "state":
        print(json.dumps({"path": str(cursor_path()), "channels": load_cursors()},
                         indent=2))
        return 0

    if args.cmd == "reset":
        cursor_path().unlink(missing_ok=True)
        print(f"cleared {cursor_path()}")
        return 0

    reader = FileSlackReader(args.history) if args.history else None
    bot_id = reader.bot_user_id if reader is not None else None
    try:
        summary = poll_once(reader, bot_id, commit_cursor=not args.no_commit,
                            max_workers=args.workers,
                            first_lookback_s=args.lookback,
                            channels=[c for c in (args.channels or "").split(",")
                                      if c.strip()] or None)
    except Exception as exc:
        from core.slack_client import SlackError

        if isinstance(exc, SlackError):
            print(f"SLACK ERROR {exc.error}")
            if exc.error == "not_allowed_token_type":
                print("  that is an app-level token (xapp-); "
                      "this needs the bot token (xoxb-)")
            elif "SLACK_BOT_TOKEN" in str(exc):
                print("  set SLACK_BOT_TOKEN in .env — see HANDOFF.md")
            return 2
        raise
    summary.pop("cursors", None)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
