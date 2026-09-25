"""Digital Twin — read and draft only, honest about what it could not check."""
from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
import unittest
from unittest import mock

from tests.helpers import FIXTURES, sandbox

PKG = pathlib.Path(__file__).resolve().parent.parent / "agents" / "digital_twin"


def _twin_config(inbox: pathlib.Path, **extra) -> dict:
    cfg = {
        "time_zone": "Asia/Bangkok",
        "sources": [
            {"name": "pasted", "kind": "file", "enabled": True, "path": str(inbox)},
            {"name": "triage", "kind": "triage", "enabled": True},
            {"name": "slack", "kind": "slack", "enabled": False, "channels": []},
        ],
        "alerts": {"paused": True, "sources": ["pasted"],
                   "notify_on": ["decision", "promise", "deadline", "blocker", "completed"],
                   "budget_per_day": 2},
    }
    cfg.update(extra)
    return cfg


def _shifted_snapshot() -> str:
    """The fixture's "ts" fields, rewritten relative to the moment the suite
    runs rather than pinned to 24 Sep 2026 — so it is neither perpetually
    "too old" (and silently invisible to a 1-day update lookback) nor, if
    naively shifted a whole calendar day, perpetually "in the future" (and
    wrongly picked up by every alert poll's `since` check, which has no
    upper bound). Everything but the calendar event moves to a few hours
    before "now" — well past the alert pilot's 1-hour first-look window, but
    inside the morning update's 1-day one. The calendar event is pinned to
    today's date at a fixed time, matching Asia/Bangkok (the fixture's own
    time zone) rather than the container's, since the two can differ right
    around midnight UTC.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Bangkok")
    now = dt.datetime.now(tz)
    offsets = {
        "slack:C0OPS:1758700000.001": now - dt.timedelta(hours=6),
        "slack:C0OPS:1758700100.002": now - dt.timedelta(hours=5, minutes=50),
        "gmail:18f2a": now - dt.timedelta(hours=7),
        "gmail:18f2b": now - dt.timedelta(hours=8),
        "cal:ev1@2026-09-24T14:00:00+07:00":
            dt.datetime.combine(now.date(), dt.time(14, 0), tzinfo=tz),
    }
    data = json.loads((FIXTURES / "twin_snapshot.json").read_text(encoding="utf-8"))
    for item in data["items"]:
        when = offsets[item["id"]]
        item["ts"] = when.isoformat()
        if item["id"].startswith("cal:"):
            item["id"] = f"cal:ev1@{item['ts']}"
    return json.dumps(data)


class _TwinCase(unittest.TestCase):
    def setUp(self):
        self._sb = sandbox()
        self.root = self._sb.__enter__()
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        (self.inbox / "snap.json").write_text(_shifted_snapshot(), encoding="utf-8")
        from core import config
        self.cfg = _twin_config(self.inbox)
        self._patch = mock.patch.object(config, "twin", lambda: self.cfg)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._sb.__exit__(None, None, None)


class ScopeTests(unittest.TestCase):
    def test_package_imports_no_write_client(self):
        """The twin drafts; it must have no path to posting or sending."""
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                for name in names:
                    self.assertNotIn("WriteClient", name, f"{path.name} imports {name}")
                    self.assertNotIn("execute", name.split(".")[-1].lower(),
                                     f"{path.name} imports {name}")

    def test_google_client_has_no_write_method(self):
        from core import google_client
        methods = [m for m in dir(google_client.GoogleReadClient) if not m.startswith("_")]
        for verb in ("send", "create", "update", "delete", "insert", "patch", "share", "modify"):
            self.assertFalse(any(verb in m for m in methods), f"{verb} in {methods}")
        src = pathlib.Path(google_client.__file__).read_text(encoding="utf-8")
        posts = src.count('method="POST"')
        self.assertEqual(posts, 1, "only the token refresh may POST")


class ProfileTests(_TwinCase):
    def test_missing_facts_stay_not_known(self):
        from agents.digital_twin import profile
        p = profile.load()
        self.assertEqual(p.name, profile.NOT_KNOWN)
        text = profile.render(p)
        self.assertIn("Name: not known", text)
        self.assertIn("Not known yet:", text)
        self.assertIn("time zone", text)

    def test_setup_walks_questions_and_skips_blank(self):
        from agents.digital_twin import profile
        # name, role, goals (one line + blank), then six skipped lists, then tz
        answers = iter(["Quenton", "Technical PM", "Ship RMA sync", "",
                        "", "", "", "", "", "", "Asia/Bangkok"])
        said = []
        p = profile.setup(ask=lambda _: next(answers), say=said.append)
        self.assertEqual(p.name, "Quenton")
        self.assertEqual(p.goals, ["Ship RMA sync"])
        self.assertEqual(p.time_zone, "Asia/Bangkok")
        self.assertIn("key people", p.missing())
        self.assertTrue(any("one question at a time" in s.lower() or "One question" in s
                            for s in said))

    def test_more_than_three_goals_becomes_an_open_question(self):
        from agents.digital_twin import profile
        p = profile.load()
        profile.apply_answer(p, "goals", "a\nb\nc\nd")
        self.assertEqual(len(p.goals), 3)
        self.assertTrue(p.open_questions)

    def test_only_a_passed_test_makes_a_source_working(self):
        from agents.digital_twin import profile, sources
        ok, _ = sources.test("pasted")
        self.assertTrue(ok)
        self.assertEqual(profile.load().source("pasted").state, profile.WORKING)
        ok, why = sources.test("slack")
        self.assertFalse(ok)
        self.assertIn("not enabled", why)
        self.assertEqual(profile.load().source("slack").state, profile.NOT_ENABLED)
        (self.inbox / "bad.json").write_text("{not json", encoding="utf-8")
        ok, _ = sources.test("pasted")
        self.assertFalse(ok)
        entry = profile.load().source("pasted")
        self.assertEqual(entry.state, profile.BLOCKED)
        self.assertTrue(entry.last_successful_test, "the last pass is kept on record")


class TaskTests(_TwinCase):
    def test_done_needs_proof(self):
        from agents.digital_twin import tasklist
        t = tasklist.add("Send the note", owner="me")
        with self.assertRaises(tasklist.NoProof):
            tasklist.update(t.task_id, status=tasklist.DONE)
        tasklist.done(t.task_id, "https://pomelo.slack.com/archives/x")
        self.assertEqual(tasklist.load(t.task_id).status, tasklist.DONE)

    def test_promise_is_kept_apart_from_proposed_date(self):
        from agents.digital_twin import tasklist
        tasklist.add("A", due="2026-09-25", due_kind=tasklist.PROMISE)
        tasklist.add("B", due="2026-09-25")
        text = tasklist.render(today=dt.date(2026, 9, 24))
        self.assertIn("(promise)", text)
        self.assertIn("(proposed)", text)
        self.assertNotIn("OVERDUE", text)
        self.assertIn("OVERDUE", tasklist.render(today=dt.date(2026, 9, 26)))

    def test_changes_keep_their_source(self):
        from agents.digital_twin import tasklist
        t = tasklist.add("A", due="2026-09-25")
        tasklist.update(t.task_id, due="2026-09-30", reason="Unni asked for more time")
        h = tasklist.load(t.task_id).history[-1]
        self.assertEqual((h["was"], h["became"]), ("2026-09-25", "2026-09-30"))
        self.assertIn("Unni", h["reason"])


class SignalTests(unittest.TestCase):
    def test_signals(self):
        from agents.digital_twin import items
        i = items.Item("s", "1", "2026-09-24T09:00:00+07:00",
                       text="We agreed to ship Friday. I'll send the note by EOD.")
        self.assertEqual(items.signals(i), ["decision", "promise"])
        routine = items.Item("s", "2", "2026-09-24T09:00:00+07:00", text="morning all")
        self.assertTrue(items.is_routine(routine))

    def test_needs_me_requires_a_name_and_an_ask(self):
        from agents.digital_twin import items
        thanks = items.Item("s", "3", "2026-09-24T09:00:00+07:00", text="thanks Quenton")
        ask = items.Item("s", "4", "2026-09-24T09:00:00+07:00",
                         text="Quenton can you approve this?")
        self.assertNotIn("needs_me", items.signals(thanks, ["Quenton"]))
        self.assertIn("needs_me", items.signals(ask, ["Quenton"]))
        self.assertNotIn("needs_me", items.signals(ask, []))


class UpdateTests(_TwinCase):
    def _profile(self):
        from agents.digital_twin import profile
        p = profile.load()
        profile.apply_answer(p, "name", "Quenton Dupont")
        profile.apply_answer(p, "time_zone", "Asia/Bangkok")
        profile.save(p)

    def test_morning_update_has_three_parts_and_says_what_was_checked(self):
        from agents.digital_twin import update
        self._profile()
        text, path = update.run("morning")
        self.assertIn("WHAT CHANGED", text)
        self.assertIn("WHAT NEEDS ME", text)
        self.assertIn("WHAT IS NEXT", text)
        self.assertIn("Checked: pasted (5 new), triage (0 new)", text)
        self.assertIn("Not enabled: slack", text)
        self.assertIn("[decision, promise]", text)
        self.assertIn("needs_me", text)              # Vishal's email
        self.assertIn("Weekly ops sync", text)       # the calendar item
        self.assertNotIn("Weekly digest", text)      # routine stays out
        self.assertIn("Asia/Bangkok", text)
        self.assertTrue(path.exists())

    def test_second_run_repeats_nothing(self):
        from agents.digital_twin import update
        self._profile()
        update.run("morning")
        text, _ = update.run("morning")
        self.assertIn("pasted (0 new)", text)

    def test_a_failed_source_is_reported_not_silent_and_keeps_its_cursor(self):
        from agents.digital_twin import cursors, update
        self._profile()
        update.run("morning")
        before = cursors.get("pasted")["through"]
        (self.inbox / "bad.json").write_text("{", encoding="utf-8")
        text, _ = update.run("morning")
        self.assertIn("Could not check: pasted", text)
        self.assertIn("last read through", text)
        self.assertEqual(cursors.get("pasted")["through"], before)
        self.assertTrue(cursors.get("pasted")["gap"])

    def test_prep_finds_related_items_after_an_update(self):
        from agents.digital_twin import update
        self._profile()
        update.run("morning")
        text = update.prep("RMA sync")
        self.assertIn("ship the RMA sync fix", text)


class DraftTests(_TwinCase):
    def test_draft_in_style_and_sent_needs_proof(self):
        from agents.digital_twin import drafts, profile, tasklist
        p = profile.load()
        profile.apply_answer(p, "writing_style", "Hi all, can we get this out today? Thanks")
        profile.save(p)
        t = tasklist.add("RMA note")
        d = drafts.new("slack", "#ops", "can you send the RMA release note today",
                       task_id=t.task_id)
        self.assertTrue(d.text.startswith("Hi,"))
        self.assertIn("today?", d.text)
        self.assertTrue(d.text.endswith("Thanks"))
        with self.assertRaises(ValueError):
            drafts.sent(d.draft_id, "")
        drafts.sent(d.draft_id, "https://pomelo.slack.com/archives/x")
        self.assertEqual(tasklist.load(t.task_id).status, tasklist.DONE)
        self.assertFalse(hasattr(drafts, "send"), "the twin never sends")


class AlertTests(_TwinCase):
    def test_paused_by_default(self):
        from agents.digital_twin import alerts
        self.assertEqual(alerts.once()["stopped"], "paused")

    def test_signals_alert_routine_stays_quiet_and_budget_stops(self):
        from agents.digital_twin import alerts, cursors
        alerts.resume()
        # the poll looks back one hour on first sight; the fixture is older
        cursors.advance("alerts:pasted", dt.datetime(2026, 9, 24, 0, 0,
                        tzinfo=dt.timezone(dt.timedelta(hours=7))), [])
        r = alerts.once()
        self.assertEqual(len(r["alerts"]), 2)              # budget_per_day = 2
        self.assertIn("budget spent", r["stopped"])
        self.assertEqual(alerts.once()["alerts"], [])       # nothing repeats

    def test_test_event_proves_the_push_path_and_dedupes(self):
        from agents.digital_twin import alerts, bridge
        alerts.resume()
        r = alerts.test_event("slack", "we decided to go with option B")
        # The same poll also surfaces the fixture's calendar event (any event
        # carries "deadline", and it's legitimately new on its first look) —
        # this test is about the push path, not about the fixture being quiet.
        pushed = [a for a in r["alerts"] if a["source"] == "slack"]
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["signals"], ["decision"])
        self.assertTrue(pushed[0]["text"].startswith("TEST"))
        self.assertEqual(bridge.status()["queued"], 0)
        self.assertTrue(bridge.push({"source": "slack", "id": "dup"}))
        self.assertFalse(bridge.push({"source": "slack", "id": "dup"}))

    def test_bridge_drops_unknown_fields_and_acks_only_its_batch(self):
        from agents.digital_twin import bridge
        bridge.push({"source": "gmail", "id": "m1", "text": "x", "password": "no"})
        self.assertNotIn("password", json.dumps(bridge.pending()))
        batch, events = bridge.claim()
        self.assertEqual(len(events), 1)
        with self.assertRaises(ValueError):
            bridge.ack("b_nope")
        self.assertEqual(bridge.ack(batch), 1)
        self.assertEqual(bridge.pending(), [])


class LeadTests(_TwinCase):
    def test_export_holds_both_records_and_no_secrets(self):
        from agents.digital_twin import lead, tasklist
        from core import config
        config.env("GOOGLE_ACCESS_TOKEN")  # register whatever is set as a secret
        tasklist.add("A task")
        path = lead.export()
        text = path.read_text(encoding="utf-8")
        self.assertIn("WORK PROFILE", text)
        self.assertIn("TASK LIST", text)
        self.assertIn("A task", text)
        self.assertNotIn("TOKEN", text)
        self.assertIn("Sources:", lead.status())

    def test_system_export_names_credentials_but_never_values(self):
        from agents.digital_twin import lead
        with mock.patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-verysecretvalue-123"}):
            text = lead.export_system().read_text(encoding="utf-8")
        self.assertIn("SLACK_BOT_TOKEN: set", text)
        self.assertNotIn("verysecretvalue", text)
        self.assertIn("SPECIFICATION — CLAUDE.md", text)
        self.assertIn("HANDOFF.md", text)
        self.assertIn("pasted (file): enabled", text)   # sources come from config/twin.yml


if __name__ == "__main__":
    unittest.main()
