import unittest

from agents.historian.retrieval import Historian
from tests.helpers import build_corpus, issue, sandbox


class HistorianTests(unittest.TestCase):
    def test_finds_the_open_duplicate(self):
        with sandbox():
            build_corpus()
            with Historian() as hist:
                f = issue("PESD1-11274")["fields"]
                dupes = hist.duplicates(f"{f['summary']} {f['description']}",
                                        exclude_ref="PESD1-11274")
            self.assertEqual(dupes[0]["ref"], "PESD1-11270")
            self.assertEqual(dupes[0]["status"], "Waiting for Support")

    def test_unrelated_ticket_has_no_duplicate(self):
        """A single weak hit must not score 1.0 — similarity is absolute."""
        with sandbox():
            build_corpus()
            with Historian() as hist:
                f = issue("PESD1-11279")["fields"]
                dupes = hist.duplicates(f"{f['summary']} {f['description']}",
                                        exclude_ref="PESD1-11279")
            self.assertEqual(dupes, [])

    def test_similar_resolved_are_actually_resolved(self):
        with sandbox():
            build_corpus()
            with Historian() as hist:
                hits = hist.similar_resolved("stock not syncing after bulk import")
            self.assertTrue(hits)
            self.assertLessEqual(len(hits), 5)
            for doc in hits:
                self.assertTrue(doc.get("resolution") or
                                doc["status"].lower() in ("done", "closed", "resolved"))

    def test_sop_outranks_raw_tickets(self):
        with sandbox():
            build_corpus()
            with Historian() as hist:
                research = hist.research("PESD1-11274",
                                         "Stock levels not updating after bulk import",
                                         "storefront shows old stock, delta file uploaded")
                evidence = research.evidence()
            self.assertTrue(research.sops)
            self.assertEqual(evidence[0]["type"], "sop")

    def test_assignee_shortlist_is_ranked_and_never_a_single_guess(self):
        with sandbox():
            build_corpus()
            with Historian() as hist:
                candidates = hist.assignee_candidates("stock sync bulk import stale",
                                                      labels=["stock-sync"])
            self.assertGreaterEqual(len(candidates), 2)
            self.assertEqual(candidates[0]["assignee"], "Somchai Prasert")
            self.assertIn("component=stock-sync", candidates[0]["reason"])
            counts = [c["count"] for c in candidates]
            self.assertEqual(counts, sorted(counts, reverse=True))

    def test_components_are_inferred_from_the_vocabulary(self):
        with sandbox():
            build_corpus()
            with Historian() as hist:
                self.assertEqual(hist.components_for("voucher rejected at checkout")[0],
                                 "checkout")
                self.assertEqual(hist.components_for("product images missing")[0],
                                 "catalog")

    def test_sop_draft_has_the_required_sections(self):
        with sandbox():
            build_corpus()
            with Historian() as hist:
                draft = hist.draft_sop("PESD1-11274",
                                       "Stock not updating after bulk import",
                                       "storefront stale, delta file uploaded",
                                       resolution="Re-ran the delta import.")
            for heading in ("## Symptom", "## Scope", "## Resolution", "## Precedent"):
                self.assertIn(heading, draft)
            self.assertIn("Re-ran the delta import.", draft)


if __name__ == "__main__":
    unittest.main()
