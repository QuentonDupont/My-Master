"""The automation guard: the third sanctioned Jira writer, and only ever an off-switch."""
from __future__ import annotations

import unittest
from unittest import mock

from core.flow import JIRA_WRITE, Agent, Context, Every, Flow, FlowError
from flows import automation_guard as G


def _a(name, effect="read"):
    return Agent(name, lambda ctx: None, effect, "test")


class GuardrailTests(unittest.TestCase):
    def test_guard_is_a_sanctioned_unattended_writer(self):
        Flow("x", [_a("guard_automation_usage", JIRA_WRITE)], triggers=[Every(3600)],
             live_when_scheduled=True)

    def test_other_writers_still_refused(self):
        with self.assertRaises(FlowError):
            _a("disable_rules", JIRA_WRITE)
        with self.assertRaises(FlowError):
            Flow("x", [_a("execute_proposal", JIRA_WRITE)], triggers=[Every(3600)],
                 live_when_scheduled=True)

    def test_flow_is_registered_hourly_and_live(self):
        from flows import registry
        f = registry()["automation-guard"]
        self.assertTrue(f.live_when_scheduled)
        self.assertEqual([a.name for a in f.agents],
                         ["estimate_automation_usage", "guard_automation_usage"])


class BehaviourTests(unittest.TestCase):
    def run_guard(self, level, execute):
        calls = []

        def fake_run(*args):
            calls.append(args)
            return {"level": level, "estimate": 1, "limit": 13800, "pct": 1.0, "reset": "2026-11-12"}

        ctx = Context("automation-guard", execute=execute, data={"usage": {"level": level}})
        with mock.patch.object(G, "_run", side_effect=fake_run):
            G.guard_automation_usage.run(ctx)
        return calls, ctx["report"]

    def test_ok_level_never_applies(self):
        calls, _ = self.run_guard("ok", execute=True)
        self.assertEqual(calls, [])

    def test_warn_and_off_apply_only_on_a_live_run(self):
        for level in ("warn", "off"):
            calls, report = self.run_guard(level, execute=True)
            self.assertEqual(calls, [("--apply",)])
            self.assertEqual(report["level"], level)
            calls, _ = self.run_guard(level, execute=False)
            self.assertEqual(calls, [], "a dry run must never switch anything")


if __name__ == "__main__":
    unittest.main()
