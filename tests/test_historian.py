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

    def test_a_shared_rare_term_surfaces_related_open_work(self):
        """The real miss: PESD1-11271 asked for two shops that already had their
        own open tickets. Word overlap was 0.43 — under any sane duplicate
        threshold — but they shared a rare proper noun and had live dev work."""
        from corpus.index import Corpus
        with sandbox():
            build_corpus()
            with Corpus() as corpus:
                corpus.add_many([
                    {"doc_id": "jira:PESD1-9001", "source_type": "jira",
                     "ref": "PESD1-9001", "project": "PESD1",
                     "status": "Waiting for Support",
                     "title": "Request to Create New Location - Central Si Racha",
                     "body": "", "links": [{"key": "PRDT-9500", "type": "Cloners",
                                            "status": "To Do"}]},
                ])
                corpus.rebuild_term_stats()
            with Historian() as hist:
                related = hist.related_open_work(
                    'Add two new locations in NS - "TH Central Si Racha" and "TH Happitat"',
                    exclude_ref="PESD1-11274")
            refs = [d["ref"] for d in related]
            self.assertIn("PESD1-9001", refs)
            hit = next(d for d in related if d["ref"] == "PESD1-9001")
            self.assertIn("racha", hit["rare_terms"])
            self.assertEqual(hit["open_clones"][0]["key"], "PRDT-9500")
            self.assertLess(hit["similarity"], 0.62,
                            "this is exactly the case a coverage threshold misses")

    def test_rarity_scales_with_the_corpus(self):
        from corpus.index import Corpus
        with sandbox():
            build_corpus()
            with Corpus() as corpus:
                self.assertTrue(corpus.is_rare_term("racha"),
                                "a term in no document is rare")
                self.assertFalse(corpus.is_rare_term("stock"),
                                 "a term in most of the fixtures is not rare")
                self.assertGreater(corpus.idf("racha"), corpus.idf("stock"))

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

    def test_terminal_statuses_from_boards_yml_count_as_precedent(self):
        """PESD1 sets no resolution on closed tickets — the status list decides."""
        from agents.historian.retrieval import Historian as H
        from core import config
        with sandbox():
            self._assert_terminal(H, config)

    def _assert_terminal(self, H, config):
        terminal = (config.boards()["intake"].get("resolved_statuses") or [])
        self.assertTrue(terminal, "boards.yml must list the terminal statuses")
        self.assertTrue(H._is_resolved({"status": terminal[0], "resolution": None}))
        self.assertTrue(H._is_resolved({"status": terminal[0].lower(),
                                        "resolution": None}))
        self.assertFalse(H._is_resolved({"status": "Waiting for Support",
                                         "resolution": None}))
        self.assertTrue(H._is_resolved({"status": "anything", "resolution": "Fixed"}))


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
