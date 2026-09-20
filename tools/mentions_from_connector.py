"""Bridge: claude.ai Slack connector search results -> a leader mentions file.

    python3 tools/mentions_from_connector.py hits.json > review/connector_mentions.json
    python3 -m agents.slack_leader.leader run \\
        --file review/connector_mentions.json --as-user U03G8QY385V

Why this exists
---------------
The Slack Leader needs to see messages addressed to a *person*. A bot token
cannot: a bot only sees channels it was invited to, never a person's DMs, and
`app_mention` events only fire for the bot's own handle. A user token (xoxp-)
solves it but has to be granted in the Slack app.

The claude.ai Slack connector is already authorised as the person and reads
exactly what they read. It is driven from a Claude session rather than from
cron, so this is a manual bridge, not a daemon — but it needs no new grant and
no app reinstall. Paste the connector's search hits in, get the leader's own
input format out.

Input: a JSON list of hits, each with at least

    {"channel": "C04P90D9K9N", "channel_name": "eng_team_api",
     "thread_ts": "1789731331.224119",       # falls back to ts
     "ts": "1789741060.435229",
     "user": "U031TP69RK3", "user_name": "Shiraz Ahmad",
     "permalink": "https://...", "text": "<@U03G8QY385V> ..."}

Hits in the same thread are merged into one mention, oldest message first, so a
thread is triaged once with its full context.

This writes nothing to Slack and holds no credentials.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: Slack search returns "<@U123|Display Name>"; conversations.history returns
#: "<@U123>". Everything downstream is written against the history form.
PIPED_MENTION = re.compile(r"<@([UWB][A-Z0-9]+)\|[^>]*>")


def normalise(text: str) -> str:
    """Search-form markup -> history-form, so both paths look identical."""
    return PIPED_MENTION.sub(r"<@\1>", text or "")


def to_mentions(hits: list[dict]) -> list[dict]:
    """Group connector hits into the leader's mention shape, one per thread."""
    threads: dict[tuple[str, str], dict] = {}

    for hit in hits:
        channel = hit.get("channel")
        if not channel:
            continue
        ts = str(hit.get("ts") or hit.get("thread_ts") or "")
        thread_ts = str(hit.get("thread_ts") or ts)
        if not thread_ts:
            continue

        key = (channel, thread_ts)
        mention = threads.get(key)
        if mention is None:
            mention = threads[key] = {
                "channel": channel,
                "channel_name": hit.get("channel_name") or channel,
                "thread_ts": thread_ts,
                "user": hit.get("user", ""),
                "user_name": hit.get("user_name") or hit.get("user", ""),
                "permalink": hit.get("permalink", ""),
                "messages": [],
            }
        mention["messages"].append({
            "ts": ts,
            "user": hit.get("user", ""),
            "text": normalise(hit.get("text", "")),
        })

    out = []
    for mention in threads.values():
        mention["messages"].sort(key=lambda m: float(m["ts"] or 0))
        # the earliest hit in the thread is the one that raised it
        first = mention["messages"][0]
        mention["user"] = first["user"] or mention["user"]
        out.append(mention)
    out.sort(key=lambda m: float(m["thread_ts"] or 0))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/mentions_from_connector.py",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("hits", help="json file of connector search hits, or - for stdin")
    ap.add_argument("-o", "--out", help="write here instead of stdout")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.hits == "-" else Path(args.hits).read_text(
        encoding="utf-8")
    hits = json.loads(raw)
    if not isinstance(hits, list):
        print("expected a json list of hits", file=sys.stderr)
        return 2

    mentions = to_mentions(hits)
    text = json.dumps(mentions, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"{len(mentions)} threads -> {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
