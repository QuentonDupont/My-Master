import unittest

from agents.jira_leader.gates import never_touch, restate
from core import config
from tests.helpers import issue


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
