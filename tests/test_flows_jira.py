"""The Jira flows: intake/triage, decisions, execute, mirror, research."""
from __future__ import annotations

import unittest

import flows
from agents.jira_leader.sources import FileTicketSource
from core import events, ledger as L, scheduler
from core.flow import Context, FlowError, Human
from flows.decisions import DECISIONS
from flows.intake import INTAKE
from flows.mirror import MIRROR
from tests.helpers import FIXTURES, build_corpus, sandbox
from tests.test_labels import FakeJira as LabelJira, proposed
from tests.test_status_mirror import FakeJira as MirrorJira, executed


def sweep():
    return INTAKE.run(source=FileTicketSource(FIXTURES / "inbox.jsonl"),
                      analyst_kind="heuristic",
                      sheet_path=str(FIXTURES / "intake_sheet.csv"))


class RegistryTests(unittest.TestCase):
    def test_every_flow_has_a_trigger_and_a_doc(self):
        reg = flows.registry()
        self.assertTrue({"intake", "decisions", "execute", "mirror", "research"} <= set(reg))
        for f in reg.values():
            self.assertTrue(f.triggers, f.name)
            self.assertTrue(f.doc, f.name)
            for a in f.agents:
                self.assertTrue(a.doc, f"{f.name}.{a.name} has no docstring")

    def test_only_execute_and_mirror_write_to_jira(self):
        writers = {f.name for f in flows.registry().values() if "jira_write" in f.effects}
        self.assertEqual(writers, {"execute", "mirror"})

    def test_execute_is_human_only_and_refuses_unconfirmed_runs(self):
        ex = flows.get("execute")
        self.assertTrue(all(isinstance(t, Human) for t in ex.triggers))
        with sandbox(), self.assertRaises(FlowError):
            scheduler.run_flow(ex)

    def test_agent_names_are_unique_within_a_flow(self):
        for f in flows.registry().values():
            names = [a.name for a in f.agents]
            self.assertEqual(len(names), len(set(names)), f.name)


class IntakeTests(unittest.TestCase):
    def test_gates_stop_triage_at_the_right_agent(self):
        with sandbox():
            build_corpus()
            res = sweep()
            self.assertTrue(res.ok)
            stopped = {r["issue"]["key"]: r["_stopped"] for r in res.data["triaged"]}
            self.assertTrue(stopped["PESD1-11277"].startswith("never_touch_gate:"))
            self.assertTrue(stopped["PESD1-11278"].startswith("sanity_gate:"))
            self.assertTrue(stopped["PESD1-11274"].startswith("duplicate_gate:"))
            self.assertIsNone(stopped["PESD1-11275"])          # ran to draft_proposal

    def test_a_sweep_emits_claimed_and_outcome_events(self):
        with sandbox():
            build_corpus()
            sweep()
            names = [e["name"] for e in events.since(0)]
            self.assertEqual(names.count("ticket.claimed"), 6)
            self.assertIn("ticket.proposed", names)
            self.assertIn("ticket.escalated", names)
            self.assertIn("ticket.duplicate", names)

    def test_second_sweep_claims_nothing(self):
        with sandbox():
            build_corpus()
            sweep()
            self.assertEqual(sweep().data["report"]["claimed"], 0)

    def test_a_crashing_agent_releases_the_ticket(self):
        from flows import triage

        with sandbox():
            build_corpus()
            saved = triage.classify.run
            boom = lambda ctx: (_ for _ in ()).throw(RuntimeError("analyst down"))  # noqa: E731
            object.__setattr__(triage.classify, "run", boom)
            try:
                res = sweep()
            finally:
                object.__setattr__(triage.classify, "run", saved)
            errors = [o for o in res.data["report"]["outcomes"] if o["state"] == "ERROR"]
            self.assertTrue(errors)
            with L.Ledger() as led:
                for o in errors:
                    row = led.get(o["ticket"])
                    self.assertEqual(row["state"], L.NEW)
                    self.assertIn("analyst down", row["last_error"])


class DecisionsTests(unittest.TestCase):
    def test_manual_run_is_dry_scheduled_run_applies(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
            jira = LabelJira({"PESD1-11280": ["triage-approved"]})
            dry = DECISIONS.run(Context("decisions", data={"reader": jira}))
            self.assertIsNone(dry.data["report"]["applied"])
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11280")["state"], L.PROPOSED)
            scheduler.run_flow(DECISIONS, trigger="schedule", data={"reader": jira})
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11280")["state"], L.APPROVED)
            self.assertIn("ticket.approved", [e["name"] for e in events.since(0)])

    def test_a_new_proposal_wakes_decisions(self):
        with sandbox():
            reg = {"decisions": DECISIONS}
            with scheduler.Store() as store:
                store.state("decisions")
                store.start_run("decisions", "schedule")    # Every(300) not due
                with L.Ledger() as led:
                    proposed(led)
                self.assertEqual(scheduler.due(DECISIONS, store), "event")
            started = scheduler.tick(reg, launcher=lambda f, t: None)
            self.assertEqual(started, [{"flow": "decisions", "trigger": "event"}])


class MirrorTests(unittest.TestCase):
    def test_moves_are_journalled_with_where_they_came_from(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
            jira = MirrorJira({"PESD1-11282": "To Do", "PRDT-11570": "In progress"})
            res = MIRROR.run(Context("mirror", execute=True,
                                     data={"reader": jira, "writer": jira}))
            self.assertTrue(res.ok)
            self.assertEqual(jira.moves, [("PESD1-11282", "In progress")])
            with L.Ledger() as led:
                j = [r for r in led.journal_for("PESD1-11282")
                     if r["step"] == "mirror_transition"]
            self.assertEqual(len(j), 1)
            self.assertIn('"from": "To Do"', j[0]["detail"])

    def test_dry_run_moves_nothing(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
            jira = MirrorJira({"PESD1-11282": "To Do", "PRDT-11570": "In progress"})
            res = MIRROR.run(Context("mirror", data={"reader": jira, "writer": jira}))
            self.assertEqual(jira.moves, [])
            self.assertFalse(res.data["report"]["moved"][0]["executed"])

    def test_execution_wakes_the_mirror(self):
        with sandbox():
            with scheduler.Store() as store:
                store.state("mirror")
                store.start_run("mirror", "schedule")
                with L.Ledger() as led:
                    executed(led)
                self.assertEqual(scheduler.due(MIRROR, store), "event")


if __name__ == "__main__":
    unittest.main()
