import unittest

from agents.jira_leader.gates import never_touch, restate
from core import config
from tests.helpers import fixture_boards, issue


def synthetic(summary: str, description: str = "") -> dict:
    return {"key": "PESD1-9999",
            "fields": {"summary": summary, "description": description,
                       "labels": [], "components": [], "reporter": {"displayName": "X"},
                       "created": "2026-09-16T09:00:00.000+0700"}}


class NeverTouchTests(unittest.TestCase):
    def test_every_configured_category_is_caught(self):
        for category, spec in config.never_touch()["categories"].items():
            term = spec["keywords"][0]
            hits = never_touch(synthetic("ticket", f"customer asked about {term} today"))
            self.assertIn(category, [h["category"] for h in hits],
                          f"{category} not caught by {term!r}")

    def test_refund_ticket_from_fixtures_is_caught(self):
        hits = never_touch(issue("PESD1-11277"))
        categories = {h["category"] for h in hits}
        self.assertIn("refunds", categories)
        self.assertIn("payments", categories)

    def test_card_shaped_digits_are_caught(self):
        hits = never_touch(synthetic("order", "the number is 4111111111111111"))
        self.assertTrue(hits)

    def test_ordinary_ticket_passes(self):
        self.assertEqual(never_touch(issue("PESD1-11279")), [])


class SanityGateTests(unittest.TestCase):
    def test_vague_ticket_is_escalated(self):
        sentence, reason = restate(issue("PESD1-11278"))
        self.assertIsNone(sentence, reason)

    def test_short_ticket_is_escalated(self):
        self.assertIsNone(restate(synthetic("help", "asap"))[0])

    def test_real_tickets_are_restated_in_one_sentence(self):
        for key in ("PESD1-11274", "PESD1-11275", "PESD1-11276", "PESD1-11279"):
            sentence, reason = restate(issue(key))
            self.assertIsNotNone(sentence, f"{key}: {reason}")
            self.assertLessEqual(len(sentence), 400)
            self.assertNotIn("\n", sentence)

    def test_question_is_phrased_as_a_question(self):
        sentence, _ = restate(issue("PESD1-11276"))
        self.assertTrue(sentence.startswith("Requester asks:"))


if __name__ == "__main__":
    unittest.main()


def setUpModule():
    global _BOARDS
    _BOARDS = fixture_boards()
    _BOARDS.__enter__()


def tearDownModule():
    _BOARDS.__exit__(None, None, None)


class YesNoQuestionTests(unittest.TestCase):
    """Slack asks yes/no questions; the wh-word list alone escalated them all."""

    def ask(self, text):
        from agents.jira_leader.gates import asks_something
        return asks_something(text)

    def test_auxiliary_openers_are_questions(self):
        for text in ("is it possible to have the netsuite location next Monday?",
                     "are you guys joining the meeting with MY?",
                     "can we get the export before the review?",
                     "did the compulsory update go out last night?",
                     "should I disable the new checkout on ios?",
                     "has the MY merchant account been switched over?"):
            self.assertTrue(self.ask(text), text)

    def test_a_declarative_question_still_counts(self):
        """Opens with neither a wh-word nor an auxiliary."""
        self.assertTrue(self.ask(
            "you guys tested the credit card payment on production clone "
            "or preproduction ?"))

    def test_wh_questions_still_work(self):
        for text in ("how do I export the daily order report?",
                     "where do I find the picking list?",
                     "which shop is affected?"):
            self.assertTrue(self.ask(text), text)

    def test_short_filler_questions_are_not_requests(self):
        for text in ("broken again?", "ok?", "really?", "?", "any?"):
            self.assertFalse(self.ask(text), text)

    def test_a_statement_is_not_a_question(self):
        for text in ("please add two new locations in NS",
                     "the storefront still shows the old numbers",
                     "I have disabled checkout and moved to the old one"):
            self.assertFalse(self.ask(text), text)

    def test_a_question_mark_alone_is_not_enough_without_content(self):
        self.assertFalse(self.ask("pls fix?"))

    def test_the_gate_now_restates_a_yes_no_request(self):
        from agents.jira_leader import gates
        from tests.helpers import fixture_boards
        issue = {"fields": {
            "summary": "is it possible to have the netsuite location for "
                       "Central Si Racha next Monday?",
            "description": "", "labels": [], "components": []}}
        with fixture_boards():
            requirement, reason = gates.restate(issue)
        self.assertIsNotNone(requirement, reason)


class NegatedActionTests(unittest.TestCase):
    """A negated action is a reported problem, whatever the verb.

    PESD1-11282 — "The return order is not syncing." — escalated as "no request
    and no reported problem" because PROBLEM_SIGNALS listed "not working",
    "not showing" and "not updating" but not this one. Enumerating phrases loses
    to the next verb every time.
    """

    def restate(self, summary, description=""):
        from agents.jira_leader import gates
        from tests.helpers import fixture_boards
        issue = {"fields": {"summary": summary, "description": description,
                            "labels": [], "components": []}}
        with fixture_boards():
            return gates.restate(issue)

    def test_the_ticket_that_exposed_this_now_restates(self):
        requirement, reason = self.restate("The return order is not syncing.")
        self.assertIsNotNone(requirement, reason)

    def test_other_negated_actions_read_as_problems(self):
        from agents.jira_leader.gates import NEGATED_ACTION
        for text in ("the page is not loading on mobile",
                     "stock is not updating after the bulk import",
                     "the invoice does not generate",
                     "the webhook isn't firing",
                     "the export didn't complete"):
            self.assertTrue(NEGATED_ACTION.search(text), text)

    def test_a_plain_request_is_not_a_problem_report(self):
        from agents.jira_leader.gates import NEGATED_ACTION
        for text in ("please add two new locations in NS",
                     "update the unit cost and total amount"):
            self.assertFalse(NEGATED_ACTION.search(text), text)

    def test_the_known_false_positive_is_accepted_deliberately(self):
        """"not planning to" reads as a negated action and passes the gate.

        This is the chosen trade-off: passing gate 2 only means the requirement
        can be stated. Never-touch still runs, the Historian still has to find
        something, and a human still approves. A false positive costs a proposal
        someone rejects; a false negative loses a real bug report in the
        escalation pile, which is what PESD1-11282 did for two days.
        """
        from agents.jira_leader.gates import NEGATED_ACTION
        self.assertTrue(NEGATED_ACTION.search("we are not planning to do this"))
