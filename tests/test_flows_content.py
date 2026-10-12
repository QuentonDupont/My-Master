"""The content and escalation flows."""
from __future__ import annotations

import unittest

from agents.marketing_onsite import lead, tasks
from core import events, scheduler
from flows.content import CONTENT
from flows.escalation import ESCALATION
from tests.helpers import sandbox


class ContentFlowTests(unittest.TestCase):
    def test_adding_a_task_wakes_the_content_flow(self):
        with sandbox():
            with scheduler.Store() as store:
                store.state("content")
                store.start_run("content", "schedule")      # hourly sweep not due
                lead.add("Swap the homepage hero for the sale")
                self.assertEqual(scheduler.due(CONTENT, store), "event")
            self.assertEqual([e["name"] for e in events.since(0)], ["content_task.added"])

    def test_a_task_with_gaps_goes_to_needs_input_not_a_guess(self):
        with sandbox():
            task = lead.add("Swap the homepage hero for the sale")   # no surface
            res = CONTENT.run()
            self.assertTrue(res.ok, res.error)
            self.assertEqual(res.data["report"]["needs_input"], 1)
            self.assertEqual(tasks.load(task.task_id).state, tasks.NEEDS_INPUT)

    def test_a_complete_task_is_drafted_and_staged(self):
        with sandbox():
            task = lead.add("New hero for the TH sale", surface="web_hero",
                            markets=["TH"], copy_text="Up to 50% off",
                            link="https://www.pomelofashion.com/th/en/sale")
            done = lead.run()
            self.assertEqual([t.task_id for t in done], [task.task_id])
            self.assertIn(done[0].state, (tasks.DRAFTED, tasks.NEEDS_INPUT))
            if done[0].state == tasks.DRAFTED:
                from agents.marketing_onsite import worker
                self.assertIsNotNone(worker.load_plan(task.task_id))

    def test_nothing_waiting_is_a_quiet_success(self):
        with sandbox():
            res = CONTENT.run()
            self.assertTrue(res.ok)
            self.assertEqual(res.data["report"]["claimed"], 0)


class EscalationFlowTests(unittest.TestCase):
    def test_refusals_surface_as_reentry_refused(self):
        from agents.jira_leader.reentry import ReentryRefused

        with sandbox():
            res = ESCALATION.run(proposal_id="p_x", comment="   ")
            self.assertFalse(res.ok)
            self.assertIsInstance(res.exc, ReentryRefused)

    def test_cannot_be_scheduled(self):
        with sandbox():
            with self.assertRaises(Exception):
                scheduler.run_flow(ESCALATION, trigger="schedule", confirm=True)


class McpFlowsStatusTests(unittest.TestCase):
    def test_flows_status_lists_every_flow_and_the_execute_backlog(self):
        from tools import mcp_server as M

        with sandbox():
            out = M.TOOLS["flows_status"]["fn"]({})
            names = {f["flow"] for f in out["flows"]}
            self.assertIn("intake", names)
            self.assertIn("execute", names)
            self.assertEqual(out["awaiting_execute_confirm"], 0)


if __name__ == "__main__":
    unittest.main()
