"""HeuristicAnalyst.analyse() ignores knowledge/rules.md entirely — only
ClaudeAnalyst reads rules_text. Found live: 2 rules were approved into
rules.md while ANTHROPIC_API_KEY was unset, so every worker ran on the
heuristic and both rules affected nothing. Not silently fixed (see
agents/jira_leader/analysis.HeuristicAnalyst's docstring) — surfaced instead,
here and on the panel's /health page.
"""
import unittest

from agents.jira_leader import analysis as A


class FakeRetrieval:
    sops = []
    similar_resolved = []
    components = []
    github = []


def _issue(key="PESD1-1"):
    return {"key": key, "fields": {"summary": "x", "description": "y",
                                   "labels": [], "comment": {"comments": []}}}


class CountApprovedRulesTests(unittest.TestCase):
    def test_placeholder_text_counts_as_zero(self):
        text = ("## Routing\n\n<!-- example -->\n\n_None approved yet._\n")
        self.assertEqual(A.count_approved_rules(text), 0)

    def test_a_bullet_is_a_real_rule(self):
        text = ("## Handling\n\n- Default to ANSWERABLE unless a dev change "
                "is actually required.\n")
        self.assertEqual(A.count_approved_rules(text), 1)

    def test_counts_across_sections(self):
        text = ("## Routing\n- Route X to Y.\n\n## Phrasing\n"
                "- Do not open with an apology.\n- Never promise a date.\n")
        self.assertEqual(A.count_approved_rules(text), 3)

    def test_empty_file_counts_as_zero(self):
        self.assertEqual(A.count_approved_rules(""), 0)


class HeuristicIgnoresRulesTests(unittest.TestCase):
    def test_the_heuristic_result_is_identical_with_or_without_rules(self):
        """This is the gap itself, made concrete: passing real rules changes
        nothing about what the heuristic decides."""
        h = A.HeuristicAnalyst()
        without = h.analyse(_issue(), FakeRetrieval(), "restated", rules_text="")
        with_rules = h.analyse(_issue(), FakeRetrieval(), "restated",
                               rules_text="- Always escalate everything.")
        self.assertEqual(without.classification, with_rules.classification)
        self.assertEqual(without.confidence, with_rules.confidence)

    def test_the_warning_fires_at_most_once_per_process(self):
        """A fresh queue-run process (what board_tick.sh spawns each tick)
        must get exactly one warning, not one per ticket it processes."""
        A._warned_rules_ignored = False
        try:
            h = A.HeuristicAnalyst()
            fired = []
            original_warn = A.LOG.warn

            def spy(event, **kw):
                if event == "analysis.rules_ignored_by_heuristic":
                    fired.append(1)
                return original_warn(event, **kw)

            A.LOG.warn = spy
            try:
                for _ in range(3):
                    h.analyse(_issue(), FakeRetrieval(), "restated",
                             rules_text="- a real rule")
            finally:
                A.LOG.warn = original_warn
            self.assertEqual(len(fired), 1)
        finally:
            A._warned_rules_ignored = False

    def test_no_warning_when_rules_md_is_effectively_empty(self):
        A._warned_rules_ignored = False
        try:
            h = A.HeuristicAnalyst()
            fired = []
            original_warn = A.LOG.warn

            def spy(event, **kw):
                if event == "analysis.rules_ignored_by_heuristic":
                    fired.append(1)
                return original_warn(event, **kw)

            A.LOG.warn = spy
            try:
                h.analyse(_issue(), FakeRetrieval(), "restated", rules_text="")
            finally:
                A.LOG.warn = original_warn
            self.assertEqual(fired, [])
        finally:
            A._warned_rules_ignored = False


if __name__ == "__main__":
    unittest.main()
