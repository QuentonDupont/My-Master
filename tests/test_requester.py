import unittest

from core import requester as R
from tests.helpers import FIXTURES, fixture_boards, issue

SHEET = R.load_sheet(FIXTURES / "intake_sheet.csv")


class RequesterRuleTests(unittest.TestCase):
    def test_rule1_jira_field_wins(self):
        match = R.resolve(issue("PESD1-11275"), SHEET)
        self.assertEqual(match.source, "jira_field")
        self.assertEqual(match.confidence, "high")
        self.assertEqual(match.email, "marketing.th@pomelofashion.com")

    def test_rule2_exact_key_join(self):
        match = R.resolve(issue("PESD1-11274"), SHEET)
        self.assertEqual((match.source, match.confidence), ("sheet", "high"))
        self.assertEqual(match.matched_row["jira_ticket"], "PESD1-11274")

    def test_rule3_needs_all_three_signals(self):
        match = R.resolve(issue("PESD1-11279"), SHEET)
        self.assertEqual((match.source, match.confidence), ("sheet", "medium"))
        self.assertTrue(match.matched_row, "medium match must show its row")
        self.assertIn("_match", match.matched_row)

    def test_rule4_never_guesses_from_a_name(self):
        match = R.resolve(issue("PESD1-11276"), SHEET)
        self.assertEqual((match.email, match.source, match.confidence),
                         (None, "unknown", "unknown"))
        self.assertIn("requester_unknown", match.flags)

    def test_timestamp_outside_the_window_is_not_a_match(self):
        one = issue("PESD1-11279")
        one["fields"]["created"] = "2026-10-30T13:30:00.000+0700"
        self.assertEqual(R.resolve(one, SHEET).source, "unknown")

    def test_no_sheet_means_unknown_not_a_guess(self):
        self.assertEqual(R.resolve(issue("PESD1-11274"), []).source, "unknown")


if __name__ == "__main__":
    unittest.main()


def setUpModule():
    global _BOARDS
    _BOARDS = fixture_boards()
    _BOARDS.__enter__()


def tearDownModule():
    _BOARDS.__exit__(None, None, None)
