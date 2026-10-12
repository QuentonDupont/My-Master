"""Source adapters — every way the twin can read, and nothing it can write.

Each adapter turns one app into `Item`s. They are built from
`config/twin.yml`; a source that is not enabled there is never constructed,
and an enabled one is only "working" once `test()` has passed and the Work
Profile's source list says so.

Kinds:
    file      snapshots the person pasted or uploaded (section C of the starter)
    triage    the triage system's own morning brief — what waits for the owner
    slack     SlackReadClient (or the fixture reader, offline)
    gmail     GoogleReadClient.messages
    calendar  GoogleReadClient.events
    drive     GoogleReadClient.modified_since

    python -m agents.digital_twin.sources list
    python -m agents.digital_twin.sources test slack
    python -m agents.digital_twin.sources read pasted --since 2026-09-23T00:00
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
from pathlib import Path

from agents.digital_twin import items as items_mod, profile as profile_mod
from agents.digital_twin.items import Item
from core import config, log

LOG = log.get("digital_twin")


class SourceError(RuntimeError):
    """A read that did not happen. The update reports it; it never says
    "no updates" for a source it could not look at."""


class Source:
    kind = "base"

    def __init__(self, name: str, spec: dict) -> None:
        self.name, self.spec = name, spec

    def read(self, since: dt.datetime) -> list[Item]:
        raise NotImplementedError

    def test(self) -> tuple[bool, str]:
        """Prove the connection works: read something real and say what."""
        try:
            got = self.read(dt.datetime.now().astimezone() - dt.timedelta(days=1))
        except SourceError as exc:
            return False, str(exc)
        return True, f"read {len(got)} item(s) from the last day"

    def limits(self) -> str:
        return ""


# -- pasted snapshots ---------------------------------------------------------

class FileSource(Source):
    """A folder of JSON snapshots. Each file is either a list of item dicts or
    {"items": [...]}; unknown fields are kept in `meta`. A snapshot is a copy
    from that moment, not a live feed, and the update says so."""
    kind = "file"

    def folder(self) -> Path:
        raw = self.spec.get("path") or "twin/inbox"
        p = Path(raw)
        return p if p.is_absolute() else config.ROOT / p

    def read(self, since: dt.datetime) -> list[Item]:
        folder = self.folder()
        if not folder.exists():
            return []
        out: list[Item] = []
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise SourceError(f"{path.name}: not valid JSON ({exc})") from None
            rows = data.get("items") if isinstance(data, dict) else data
            for i, row in enumerate(rows or []):
                item = Item(
                    source=self.name,
                    item_id=str(row.get("id") or row.get("item_id") or f"{path.stem}#{i}"),
                    ts=str(row.get("ts") or row.get("date") or _mtime(path)),
                    author=str(row.get("author") or row.get("from") or ""),
                    title=str(row.get("title") or row.get("subject") or ""),
                    text=str(row.get("text") or row.get("body") or ""),
                    link=str(row.get("link") or ""),
                    kind=str(row.get("kind") or "note"),
                    meta={"snapshot": path.name, **(row.get("meta") or {})},
                )
                when = item.when
                if when is None or when >= since:
                    out.append(item)
        return out

    def limits(self) -> str:
        return "snapshot only — a copy from when it was pasted, not live"


def _mtime(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
        timespec="seconds")


# -- the triage system --------------------------------------------------------

class TriageSource(Source):
    """What the support triage system is waiting on the owner for. Reads the
    ledger through the Chief of Staff's brief; never touches Jira itself."""
    kind = "triage"

    def read(self, since: dt.datetime) -> list[Item]:
        from agents.chief_of_staff import brief as brief_mod
        try:
            with_boards = self.spec.get("boards", False)
            data = brief_mod.brief() if with_boards else _brief_without_boards(brief_mod)
        except Exception as exc:  # the ledger may not exist yet
            raise SourceError(f"triage brief unavailable: {exc}") from None
        now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        out: list[Item] = []
        for row in data["waiting_for_you"]:
            out.append(Item(self.name, f"proposal:{row['proposal']}", now,
                            title=f"{row['ticket']} — proposal waiting for your decision",
                            text=f"{row.get('classification', '')}: {row.get('requirement', '')}"
                                 + (f" ⚑ {', '.join(row['flags'])}" if row.get("flags") else "")
                                 + " Can you approve or reject this?",
                            link=config.ticket_url(row["ticket"]), kind="note",
                            meta={"section": "waiting"}))
        for row in data["escalations"]:
            out.append(Item(self.name, f"escalation:{row['ticket']}", now,
                            title=f"{row['ticket']} — escalated, needs you not the system",
                            text=f"{row.get('requirement', '')} Please decide.",
                            link=config.ticket_url(row["ticket"]), kind="note",
                            meta={"section": "escalation"}))
        return out


def _brief_without_boards(brief_mod):
    """The brief without the live board ranking — that needs a Jira token and
    the twin's update should not stall on it."""
    saved = brief_mod._board_section
    brief_mod._board_section = lambda: {}
    try:
        return brief_mod.brief()
    finally:
        brief_mod._board_section = saved


# -- slack --------------------------------------------------------------------

class SlackSource(Source):
    kind = "slack"

    def __init__(self, name: str, spec: dict, client=None) -> None:
        super().__init__(name, spec)
        self._client = client

    def client(self):
        if self._client is None:
            from core.slack_client import SlackReadClient
            self._client = SlackReadClient()
        return self._client

    def read(self, since: dt.datetime) -> list[Item]:
        allowed = [str(c) for c in (self.spec.get("channels") or [])]
        if not allowed:
            return []  # nothing allowed is nothing read — never "everything"
        from core.slack_client import SlackError
        client = self.client()
        try:
            convs = {c["id"]: c for c in client.conversations()}
            oldest = f"{since.timestamp():.6f}"
            out: list[Item] = []
            names: dict[str, str] = {}
            for chan_id in allowed:
                conv = convs.get(chan_id) or next(
                    (c for c in convs.values() if c.get("name") == chan_id), None)
                if not conv:
                    raise SourceError(f"channel {chan_id} is not visible to this token")
                for msg in client.history(conv["id"], oldest=oldest):
                    uid = msg.get("user", "")
                    if uid and uid not in names:
                        u = client.user(uid)
                        names[uid] = (u.get("profile") or {}).get("real_name") or u.get("name") or uid
                    ts = msg.get("ts", "0")
                    out.append(Item(
                        self.name, f"{conv['id']}:{ts}",
                        dt.datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds"),
                        author=names.get(uid, uid), title=f"#{conv.get('name', conv['id'])}",
                        text=msg.get("text", ""), link=_permalink(client, conv["id"], ts),
                        kind="message", meta={"channel": conv["id"], "thread_ts": msg.get("thread_ts", "")}))
            return out
        except SlackError as exc:
            raise SourceError(f"slack: {exc}") from None

    def limits(self) -> str:
        return ("only the channels listed in config/twin.yml; a bot token cannot "
                "see DMs, a user token sees what the person sees")


def _permalink(client, channel: str, ts: str) -> str:
    try:
        return client.permalink(channel, ts)
    except Exception:
        return ""


# -- google -------------------------------------------------------------------

class _GoogleSource(Source):
    def __init__(self, name: str, spec: dict, client=None) -> None:
        super().__init__(name, spec)
        self._client = client

    def client(self):
        if self._client is None:
            from core.google_client import GoogleReadClient
            self._client = GoogleReadClient()
        return self._client

    def _guard(self, fn):
        from core.google_client import GoogleError
        try:
            return fn()
        except GoogleError as exc:
            raise SourceError(f"{self.name}: {exc}") from None


class GmailSource(_GoogleSource):
    kind = "gmail"

    def read(self, since: dt.datetime) -> list[Item]:
        query = self.spec.get("query") or "newer_than:1d"
        query += f" after:{int(since.timestamp())}"
        rows = self._guard(lambda: self.client().messages(query))
        out = []
        for m in rows:
            ms = m.get("internal_ms") or 0
            when = (dt.datetime.fromtimestamp(ms / 1000).astimezone() if ms
                    else _parse_rfc2822(m.get("date", "")))
            out.append(Item(self.name, m["id"], when.isoformat(timespec="seconds"),
                            author=m.get("from", ""), title=m.get("subject", ""),
                            text=m.get("snippet", ""), link=m.get("link", ""),
                            kind="email", meta={"thread_id": m.get("thread_id", "")}))
        return out

    def limits(self) -> str:
        return f"headers and snippet only, query: {self.spec.get('query') or 'newer_than:1d'}"


class CalendarSource(_GoogleSource):
    """Events from now until `days_ahead` (default 7). Not filtered by `since`:
    a meeting tomorrow matters whether or not it was created since the last
    read. Dedupe still applies per event id + start."""
    kind = "calendar"

    def read(self, since: dt.datetime) -> list[Item]:
        now = dt.datetime.now().astimezone()
        horizon = now + dt.timedelta(days=int(self.spec.get("days_ahead") or 7))
        rows = self._guard(lambda: self.client().events(
            now, horizon, self.spec.get("calendar_id") or "primary"))
        out = []
        for ev in rows:
            start = (ev.get("start") or {})
            when = start.get("dateTime") or (start.get("date", "") + "T00:00:00")
            end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date", "")
            attendees = [a.get("email", "") for a in ev.get("attendees") or []]
            out.append(Item(self.name, f"{ev.get('id')}@{when}", _with_tz(when),
                            author=(ev.get("organizer") or {}).get("email", ""),
                            title=ev.get("summary", "(no title)"),
                            text=ev.get("description", ""), link=ev.get("htmlLink", ""),
                            kind="event", meta={"end": end, "attendees": attendees,
                                                "location": ev.get("location", "")}))
        return out


class DriveSource(_GoogleSource):
    kind = "drive"

    def read(self, since: dt.datetime) -> list[Item]:
        watched = [str(f) for f in (self.spec.get("files") or [])]
        if not watched:
            return []
        out = []
        for ref in watched:
            rows = self._guard(lambda ref=ref: self.client().modified_since(since, folder_id=ref))
            if not rows:  # maybe a file id rather than a folder
                f = self._guard(lambda ref=ref: self.client().file(ref))
                rows = [f] if f.get("modifiedTime", "") >= since.astimezone(
                    dt.timezone.utc).isoformat() else []
            for f in rows:
                out.append(Item(self.name, f"{f['id']}@{f.get('modifiedTime', '')}",
                                _with_tz(f.get("modifiedTime", "")),
                                author=(f.get("lastModifyingUser") or {}).get("displayName", ""),
                                title=f.get("name", ""), text=f"changed: {f.get('mimeType', '')}",
                                link=f.get("webViewLink", ""), kind="file_change",
                                meta={"file_id": f["id"], "watched": ref}))
        return out

    def limits(self) -> str:
        return "a change notice is not the document; only listed files/folders"


def _parse_rfc2822(value: str) -> dt.datetime:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return dt.datetime.now().astimezone()


def _with_tz(value: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


# -- registry -----------------------------------------------------------------

KINDS = {
    "file": FileSource, "triage": TriageSource, "slack": SlackSource,
    "gmail": GmailSource, "calendar": CalendarSource, "drive": DriveSource,
}


def specs() -> list[dict]:
    return list(config.twin().get("sources") or [])


def build(name: str, clients: dict | None = None) -> Source:
    """Construct one source by name. Refuses one that is not enabled, so a
    disabled app cannot be read by accident from another command."""
    for spec in specs():
        if spec.get("name") == name:
            if not spec.get("enabled"):
                raise SourceError(f"{name} is not enabled in config/twin.yml")
            cls = KINDS.get(spec.get("kind", ""))
            if cls is None:
                raise SourceError(f"{name}: unknown kind {spec.get('kind')!r}")
            client = (clients or {}).get(name)
            return cls(name, spec, client) if client is not None else cls(name, spec)
    raise SourceError(f"no source named {name} in config/twin.yml")


def enabled(clients: dict | None = None) -> list[Source]:
    return [build(s["name"], clients) for s in specs() if s.get("enabled")]


def test(name: str, clients: dict | None = None) -> tuple[bool, str]:
    """Test one source and record the result on the Work Profile."""
    try:
        src = build(name, clients)
    except SourceError as exc:
        profile_mod.set_not_enabled(name)
        return False, str(exc)
    ok, detail = src.test()
    profile_mod.record_test(name, ok, limits=src.limits(),
                            allowed=[str(v) for v in
                                     (src.spec.get("channels") or src.spec.get("files")
                                      or [src.spec.get("query") or src.spec.get("path") or ""])
                                     if v])
    LOG.info("source.test", source=name, ok=ok, detail=detail[:200])
    return ok, detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Digital Twin sources.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("test")
    t.add_argument("name")
    r = sub.add_parser("read")
    r.add_argument("name")
    r.add_argument("--since", default="", help="ISO datetime; default one day back")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for spec in specs():
            print(f"{spec.get('name', '?'):10} {spec.get('kind', '?'):9} "
                  f"{'enabled' if spec.get('enabled') else 'off'}")
        return 0
    if args.cmd == "test":
        ok, detail = test(args.name)
        print(f"{args.name}: {'working' if ok else 'blocked'} — {detail}")
        return 0 if ok else 1
    if args.cmd == "read":
        since = (dt.datetime.fromisoformat(args.since).astimezone() if args.since
                 else dt.datetime.now().astimezone() - dt.timedelta(days=1))
        for item in build(args.name).read(since):
            print(json.dumps(item.to_dict(), ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
