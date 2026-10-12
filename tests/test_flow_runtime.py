"""The flow runtime: agents, flows, triggers, events and the scheduler."""
from __future__ import annotations

import datetime as dt
import unittest

from core import events, ledger as ledger_mod, scheduler
from core.flow import (JIRA_WRITE, READ, Agent, Context, Daily, Every, Flow,
                       FlowError, ForEach, Human, On, Step, agent)
from tests.helpers import fixture_boards, sandbox


class Sandboxed(unittest.TestCase):
    """Flow runs log to logs/; keep them out of the real repo's logs."""

    def setUp(self):
        import contextlib
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(sandbox())

    def tearDown(self):
        self._stack.close()


def _a(name, fn=lambda ctx: None, effect="local"):
    return Agent(name, fn, effect)


class GuardrailTests(Sandboxed):
    def test_only_sanctioned_agents_may_write_to_jira(self):
        with self.assertRaises(FlowError):
            _a("post_comment", effect=JIRA_WRITE)
        _a("execute_proposal", effect=JIRA_WRITE)
        _a("mirror_parent_status", effect=JIRA_WRITE)

    def test_execute_proposal_cannot_be_scheduled(self):
        ex = _a("execute_proposal", effect=JIRA_WRITE)
        for trig in (Every(60), On("ticket.approved"), Daily("09:00")):
            with self.assertRaises(FlowError):
                Flow("x", [ex], triggers=[trig])
        with self.assertRaises(FlowError):
            Flow("x", [ex], triggers=[Human(), Every(60)])
        Flow("x", [ex], triggers=[Human()])

    def test_execute_proposal_inside_a_fan_out_is_still_caught(self):
        sub = Flow("one", [_a("execute_proposal", effect=JIRA_WRITE)])
        with self.assertRaises(FlowError):
            Flow("x", [ForEach("each", "items", sub, lambda c, i: {})],
                 triggers=[Every(60)])

    def test_unknown_effect_refused(self):
        with self.assertRaises(FlowError):
            _a("x", effect="apollo")

    def test_human_only_flow_needs_confirm(self):
        with sandbox():
            f = Flow("h", [_a("a")], triggers=[Human()])
            with self.assertRaises(FlowError):
                scheduler.run_flow(f)
            with self.assertRaises(FlowError):
                scheduler.run_flow(f, trigger="schedule", confirm=True)
            self.assertTrue(scheduler.run_flow(f, confirm=True).ok)

    def test_scheduled_runs_are_dry_unless_flow_is_live(self):
        seen = {}
        a = _a("look", lambda ctx: seen.setdefault(ctx.flow, ctx.execute))
        with sandbox():
            scheduler.run_flow(Flow("dry", [a], triggers=[Every(60)]),
                               trigger="schedule", execute=True)
            scheduler.run_flow(Flow("live", [a], triggers=[Every(60)],
                                    live_when_scheduled=True), trigger="schedule")
        self.assertEqual(seen, {"dry": False, "live": True})


class RunTests(Sandboxed):
    def test_agents_share_context_in_order(self):
        @agent("first", READ)
        def first(ctx):
            """put a value"""
            ctx["n"] = 1

        @agent("second")
        def second(ctx):
            ctx["n"] += 1

        self.assertEqual(first.doc, "put a value")
        res = Flow("f", [first, second]).run()
        self.assertTrue(res.ok)
        self.assertEqual(res.data["n"], 2)
        self.assertEqual([s.agent for s in res.steps], ["first", "second"])

    def test_stop_ends_flow_successfully(self):
        ran = []
        res = Flow("f", [_a("gate", lambda ctx: ctx.stop("decided")),
                         _a("after", lambda ctx: ran.append(1))]).run()
        self.assertTrue(res.ok)
        self.assertEqual(res.stopped, "gate: decided")
        self.assertEqual(ran, [])

    def test_failure_stops_and_calls_on_failure(self):
        hooked = []

        def boom(ctx):
            raise ValueError("nope")

        f = Flow("f", [_a("boom", boom), _a("after", lambda c: hooked.append("ran"))],
                 on_failure=lambda ctx, exc: hooked.append(str(exc)))
        res = f.run()
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "boom: nope")
        self.assertEqual(hooked, ["nope"])

    def test_continue_on_error_runs_the_rest(self):
        ran = []

        def boom(ctx):
            raise RuntimeError("x")

        res = Flow("f", [Step(_a("boom", boom), on_error="continue"),
                         _a("after", lambda c: ran.append(1))]).run()
        self.assertFalse(res.ok)
        self.assertEqual(ran, [1])
        self.assertEqual(res.errors, ["boom: x"])

    def test_for_each_fans_out_and_isolates_failures(self):
        def work(ctx):
            if ctx["item"] == 2:
                raise RuntimeError("bad item")
            ctx["out"] = ctx["item"] * 10

        sub = Flow("one", [_a("work", work)])
        f = Flow("many", [ForEach("each", "items", sub,
                                  lambda c, i: {"item": i}, max_workers=3)])
        res = f.run(items=[1, 2, 3])
        self.assertTrue(res.ok)
        got = sorted((r["item"], r.get("out"), r["_ok"]) for r in res.data["results"])
        self.assertEqual(got, [(1, 10, True), (2, None, False), (3, 30, True)])

    def test_describe_lists_triggers_and_effects(self):
        f = Flow("f", [_a("a", effect=READ)], triggers=[Every(300), On("ticket.")])
        d = f.describe()
        self.assertEqual(d["triggers"], ["every 5m", "on ticket."])
        self.assertEqual(d["steps"], ["a [read]"])


class TriggerTests(Sandboxed):
    def test_daily_slot(self):
        t = Daily("09:00", weekdays=(0,))
        monday = dt.datetime(2026, 10, 12, 9, 30)
        self.assertEqual(t.slot(monday), monday.replace(minute=0))
        self.assertIsNone(t.slot(monday.replace(hour=8)))
        self.assertIsNone(t.slot(monday + dt.timedelta(days=1)))
        self.assertEqual(t.describe(), "on Mon at 09:00")
        self.assertIsNone(t.slot(monday.replace(hour=12, minute=1)))   # past grace

    def test_on_prefix(self):
        self.assertTrue(On("ticket.").matches("ticket.approved"))
        self.assertFalse(On("ticket.approved").matches("ticket.approved_x"))


class EventTests(unittest.TestCase):
    def test_ledger_transitions_emit_events(self):
        with sandbox(), fixture_boards():
            with ledger_mod.Ledger() as led:
                led.claim("PESD1-1", "h")
                led.transition("PESD1-1", ledger_mod.PROPOSED, proposal_id="p_1")
                led.transition("PESD1-1", ledger_mod.APPROVED)
            names = [(e["name"], e["ticket_key"]) for e in events.since(0)]
            self.assertEqual(names, [("ticket.claimed", "PESD1-1"),
                                     ("ticket.proposed", "PESD1-1"),
                                     ("ticket.approved", "PESD1-1")])
            self.assertEqual(events.since(0)[-1]["data"],
                             {"previous": "PROPOSED", "proposal_id": "p_1"})

    def test_refused_transition_emits_nothing(self):
        with sandbox():
            with ledger_mod.Ledger() as led:
                led.claim("PESD1-1", "h")
                with self.assertRaises(ledger_mod.LedgerError):
                    led.transition("PESD1-1", ledger_mod.EXECUTED)
            self.assertEqual([e["name"] for e in events.since(0)], ["ticket.claimed"])

    def test_emit(self):
        with sandbox():
            events.emit("content_task.added", task="c_1")
            e = events.since(0)[0]
            self.assertEqual((e["name"], e["data"]), ("content_task.added", {"task": "c_1"}))


class SchedulerTests(unittest.TestCase):
    def _reg(self, *flows):
        return {f.name: f for f in flows}

    def test_every_is_due_then_not(self):
        with sandbox():
            f = Flow("f", [_a("a")], triggers=[Every(300)])
            launched = []
            reg = self._reg(f)

            def inline(flow, trig):
                launched.append(trig)
                scheduler.run_flow(flow, trigger=trig)

            scheduler.tick(reg, launcher=inline)
            scheduler.tick(reg, launcher=inline)
            self.assertEqual(launched, ["schedule"])
            later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=301)
            scheduler.tick(reg, launcher=inline, now=later)
            self.assertEqual(launched, ["schedule", "schedule"])

    def test_human_flows_never_ticked(self):
        with sandbox():
            launched = []
            scheduler.tick(self._reg(Flow("h", [_a("a")], triggers=[Human()])),
                           launcher=lambda f, t: launched.append(f.name))
            self.assertEqual(launched, [])

    def test_events_wake_a_flow_once_and_history_is_not_replayed(self):
        with sandbox():
            events.emit("ticket.executed", "PESD1-0")      # before the flow existed
            seen = []
            f = Flow("m", [_a("a", lambda ctx: seen.append(
                [e["ticket_key"] for e in ctx.events]))], triggers=[On("ticket.executed")])
            reg = self._reg(f)

            def inline(flow, trig):
                scheduler.run_flow(flow, trigger=trig)

            scheduler.tick(reg, launcher=inline)          # registers the cursor
            self.assertEqual(seen, [])
            events.emit("ticket.executed", "PESD1-1")
            events.emit("ticket.approved", "PESD1-2")     # not subscribed
            scheduler.tick(reg, launcher=inline)
            scheduler.tick(reg, launcher=inline)
            self.assertEqual(seen, [["PESD1-1"]])

    def test_failed_event_run_keeps_events_and_backs_off(self):
        with sandbox():
            calls = []

            def flaky(ctx):
                calls.append(len(ctx.events))
                if len(calls) == 1:
                    raise RuntimeError("down")

            f = Flow("m", [_a("a", flaky)], triggers=[On("x")])
            reg = self._reg(f)
            inline = lambda flow, trig: scheduler.run_flow(flow, trigger=trig)  # noqa: E731
            scheduler.tick(reg, launcher=inline)
            events.emit("x")
            scheduler.tick(reg, launcher=inline)          # fails
            scheduler.tick(reg, launcher=inline)          # backing off
            self.assertEqual(calls, [1])
            later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                seconds=scheduler.EVENT_RETRY_SECONDS + 1)
            scheduler.tick(reg, launcher=inline, now=later)
            self.assertEqual(calls, [1, 1])               # same event, retried
            scheduler.tick(reg, launcher=inline, now=later)
            self.assertEqual(calls, [1, 1])               # consumed after success

    def test_lock_prevents_overlap_and_expires(self):
        with sandbox():
            f = Flow("f", [_a("a")], triggers=[Every(60)], timeout_seconds=60)
            with scheduler.Store() as store:
                self.assertTrue(store.acquire(f, "one"))
                self.assertFalse(store.acquire(f, "two"))
                self.assertTrue(store.locked(f))
                res = scheduler.run_flow(f, trigger="schedule")
                self.assertEqual(res.stopped, "already running")
                store.conn.execute("UPDATE flow_state SET locked_at = '2000-01-01T00:00:00+00:00'")
                self.assertFalse(store.locked(f))
                self.assertTrue(store.acquire(f, "two"))

    def test_runs_recorded_and_status(self):
        with sandbox():
            f = Flow("f", [_a("a")], triggers=[Every(60)])
            scheduler.run_flow(f)
            st = scheduler.status({"f": f})[0]
            self.assertTrue(st["last_run"]["ok"])
            self.assertFalse(st["running"])
            with scheduler.Store() as store:
                self.assertEqual(len(store.runs("f")), 1)

    def test_daily_runs_once_per_slot(self):
        with sandbox():
            f = Flow("d", [_a("a")], triggers=[Daily("09:00")])
            reg = {"d": f}
            launched = []
            inline = lambda flow, trig: (launched.append(1),  # noqa: E731
                                         scheduler.run_flow(flow, trigger=trig))
            local = dt.datetime.now().astimezone()
            before = local.replace(hour=8, minute=59, second=0, microsecond=0)
            after = local.replace(hour=9, minute=1, second=0, microsecond=0)
            scheduler.tick(reg, launcher=inline, now=before)
            self.assertEqual(launched, [])
            if dt.datetime.now().astimezone() >= after:
                scheduler.tick(reg, launcher=inline, now=after)
                scheduler.tick(reg, launcher=inline, now=after)
                self.assertEqual(launched, [1])


class ContextTests(Sandboxed):
    def test_context_access(self):
        ctx = Context("f", data={"a": 1})
        ctx["b"] = 2
        self.assertIn("b", ctx)
        self.assertEqual(ctx.get("c", 3), 3)


if __name__ == "__main__":
    unittest.main()
