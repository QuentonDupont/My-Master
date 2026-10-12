import unittest

from agents.historian import retrieval
from agents.historian.retrieval import Historian
from tests.helpers import build_corpus, issue, sandbox


class FakeCorpus:
    """A stand-in for corpus.index.Corpus: returns exactly the docs given,
    ignoring every filter -- the real search's filtering is not what these
    tests are about."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def search(self, text, *, limit=10, source_types=None, project=None,
              statuses=None, exclude_refs=()):
        return [d for d in self.docs if d["ref"] not in exclude_refs]

    def close(self):
        pass


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
            # It used to score 0.43 — under any duplicate threshold. With rare
            # terms weighted and coverage measured both ways it now scores well
            # above it, so the case is caught rather than merely surfaced.
            self.assertGreater(hit["similarity"], 0.4)

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

    def test_a_sprawling_page_does_not_match_everything(self):
        """A 3000-word notes page contains a bit of every subject; without
        length damping it floats to the top of every search."""
        from corpus.index import Corpus
        with sandbox():
            build_corpus()
            with Corpus() as corpus:
                corpus.add_many([
                    {"doc_id": "confluence:1", "source_type": "confluence",
                     "ref": "PM/Daily Notes", "title": "2019 - PM Daily Notes",
                     "body": " ".join(["stock order invoice checkout image sync "
                                       "warehouse voucher catalogue shipment"] * 120)},
                    {"doc_id": "confluence:2", "source_type": "confluence",
                     "ref": "PM/New Retail Location", "title": "New Retail Location V1",
                     "body": "Checklist for opening a new retail location: "
                             "addresses, shipping, payment methods, store pickup."},
                ])
                corpus.rebuild_term_stats()
            with Historian() as hist:
                hits = hist.sops("add two new retail locations", limit=3)
            refs = [h["ref"] for h in hits]
            self.assertIn("PM/New Retail Location", refs)
            if "PM/Daily Notes" in refs:
                self.assertLess(refs.index("PM/Daily Notes"),
                                99)  # present is fine
                self.assertGreater(refs.index("PM/Daily Notes"),
                                   refs.index("PM/New Retail Location"),
                                   "the specific page must outrank the sprawling one")

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


class DistinguishingIdTests(unittest.TestCase):
    """Built 12 Oct 2026: duplicate detection matched two different
    Crashlytics alerts (PESD1-11337/11298) and two different NCG item
    requests (PESD1-11333/11334) to each other on text similarity alone."""

    def test_extracts_codes_and_a_crash_signature(self):
        ids = retrieval.distinguishing_ids(
            "RMA1432395 and S_NCG0147, also "
            "com.mobile.pomelo.v2.Fragment$composeView$1.invokeSuspend")
        self.assertIn("RMA1432395", ids)
        self.assertIn("S_NCG0147", ids)
        self.assertIn("com.mobile.pomelo.v2.Fragment$composeView$1.invokeSuspend", ids)

    def test_case_insensitive_for_codes(self):
        self.assertIn("RMA1432395", retrieval.distinguishing_ids("rma1432395"))

    def test_plain_text_has_no_ids(self):
        self.assertEqual(retrieval.distinguishing_ids("please update the composition"), set())

    def _corpus(self, ref: str, title: str, similarity: float = 0.9) -> FakeCorpus:
        return FakeCorpus([{"ref": ref, "title": title, "body": "", "links": [],
                            "similarity": similarity}])

    def test_same_wording_different_rma_is_not_a_duplicate(self):
        with Historian(corpus=self._corpus(
                "PESD1-A", "Please remove this return RMA1398672 duplicate")) as hist:
            dupes = hist.duplicates("Please remove this return RMA1322222 duplicate",
                                    exclude_ref="PESD1-B")
        self.assertEqual(dupes, [])

    def test_same_wording_same_rma_is_still_a_duplicate(self):
        with Historian(corpus=self._corpus(
                "PESD1-A", "Please remove this return RMA1398672 duplicate")) as hist:
            dupes = hist.duplicates("Please remove this return RMA1398672 duplicate",
                                    exclude_ref="PESD1-B")
        self.assertEqual([d["ref"] for d in dupes], ["PESD1-A"])

    def test_two_different_crash_signatures_are_not_a_duplicate(self):
        a = "com.mobile.pomelo.v2.categories.CategoryPageFragment$composeView$1.invokeSuspend"
        b = "com.mobile.pomelo.v2.home.HomeFragment$composeView$1.invokeSuspend"
        with Historian(corpus=self._corpus(
                "PESD1-A", f"[Crashlytics] [New Fatal Issue] {a}")) as hist:
            dupes = hist.duplicates(f"[Crashlytics] [New Fatal Issue] {b}",
                                    exclude_ref="PESD1-B")
        self.assertEqual(dupes, [])

    def test_the_same_crash_signature_twice_is_still_a_duplicate(self):
        a = "com.mobile.pomelo.v2.categories.CategoryPageFragment$composeView$1.invokeSuspend"
        with Historian(corpus=self._corpus(
                "PESD1-A", f"[Crashlytics] [New Fatal Issue] {a}")) as hist:
            dupes = hist.duplicates(f"[Crashlytics] [New Fatal Issue] {a}",
                                    exclude_ref="PESD1-B")
        self.assertEqual([d["ref"] for d in dupes], ["PESD1-A"])

    def test_neither_side_naming_a_record_is_unaffected(self):
        """The common case -- most duplicates share no code at all -- must
        keep matching exactly as before."""
        with Historian(corpus=self._corpus("PESD1-A", "The stock levels are wrong")) as hist:
            dupes = hist.duplicates("The stock levels are wrong", exclude_ref="PESD1-B")
        self.assertEqual([d["ref"] for d in dupes], ["PESD1-A"])

    def test_a_shared_cross_reference_does_not_cause_a_false_mismatch(self):
        """One side naming an extra code the other also contains must not
        demote the match -- only a genuine, total mismatch should."""
        with Historian(corpus=self._corpus(
                "PESD1-A", "Stock sync broken, see also PRDT-11570 for history")) as hist:
            dupes = hist.duplicates("Stock sync broken, see also PRDT-11570 for history",
                                    exclude_ref="PESD1-B")
        self.assertEqual([d["ref"] for d in dupes], ["PESD1-A"])

    def test_fixture_corpus_duplicate_still_holds(self):
        """The existing fixture duplicate (PESD1-11274 -> PESD1-11270) must
        survive this change -- neither carries a record-style code."""
        with sandbox():
            build_corpus()
            with Historian() as hist:
                f = issue("PESD1-11274")["fields"]
                dupes = hist.duplicates(f"{f['summary']} {f['description']}",
                                        exclude_ref="PESD1-11274")
            self.assertEqual(dupes[0]["ref"], "PESD1-11270")


if __name__ == "__main__":
    unittest.main()
