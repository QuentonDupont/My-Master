import unittest

from agents.chief_of_staff import brief as brief_mod, rules as rules_mod
from core import corrections
from tests.helpers import sandbox


class RuleProposalTests(unittest.TestCase):
    def test_repeated_reassignment_becomes_a_routing_proposal(self):
        with sandbox():
            for i in range(3):
                corrections.record(f"p_000{i}", "clone.assignee", "Pim Wattana",
                                   "Somchai Prasert", "Pim is on leave")
            proposed = rules_mod.cluster()
            routing = [r for r in proposed if r["kind"] == "routing"]
            self.assertEqual(len(routing), 1)
            self.assertIn("Somchai Prasert", routing[0]["rule"])
            self.assertEqual(routing[0]["support"], 3)

    def test_single_occurrence_is_not_proposed(self):
        with sandbox():
            corrections.record("p_0001", "clone.assignee", "A", "B", "once")
            self.assertEqual([r for r in rules_mod.cluster() if r["kind"] == "routing"],
                             [])

    def test_phrasing_edits_are_clustered(self):
        with sandbox():
            for i in range(2):
                corrections.record(
                    f"p_010{i}", "proposed_comment",
                    "We sincerely apologise for the inconvenience. Here is the answer.",
                    "Here is the answer.", "tone")
            phrasing = [r for r in rules_mod.cluster() if r["kind"] == "phrasing"]
            self.assertTrue(phrasing)
            self.assertIn("apologise", phrasing[0]["rule"])

    def test_rule_proposals_are_written_to_review_not_to_rules_md(self):
        with sandbox():
            for i in range(2):
                corrections.record(f"p_020{i}", "clone.assignee", "A", "B", "why")
            path = rules_mod.write_proposal_file()
            self.assertIn("review", path)
            self.assertIn("rule_proposals", path)
            from core import config
            self.assertFalse((config.KNOWLEDGE_DIR / "rules.md").exists(),
                             "the Chief of Staff must never write rules.md")


class BriefTests(unittest.TestCase):
    def test_brief_renders_with_an_empty_system(self):
        with sandbox():
            data = brief_mod.brief()
            text = brief_mod.render(data)
            self.assertIn("Morning brief", text)
            self.assertIn("0 proposals waiting for you", text)

    def test_cost_is_zero_without_llm_calls(self):
        with sandbox():
            self.assertEqual(brief_mod.cost(7)["total_usd"], 0)


if __name__ == "__main__":
    unittest.main()
