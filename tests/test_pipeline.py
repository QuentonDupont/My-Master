"""End-to-end: queue -> workers -> batch -> apply -> dry-run execute, offline."""
import json
import pathlib
import unittest

from agents.jira_leader import batch as batch_mod, queue as queue_mod
from agents.jira_leader.sources import FileTicketSource
from core import config, ledger as L, proposals as P
from tests.helpers import FIXTURES, build_corpus, sandbox


def run_queue():
    return queue_mod.run(FileTicketSource(FIXTURES / "inbox.jsonl"),
                         analyst_kind="heuristic",
                         sheet_path=str(FIXTURES / "intake_sheet.csv"))


class PipelineTests(unittest.TestCase):
    def test_full_triage_run(self):
        with sandbox():
            build_corpus()
            summary = run_queue()
            states = {o["ticket"]: o["state"] for o in summary["outcomes"]}

            self.assertEqual(summary["claimed"], 6)
            self.assertEqual(states["PESD1-11274"], L.DUPLICATE)   # open duplicate
            self.assertEqual(states["PESD1-11275"], L.PROPOSED)    # checkout bug
            self.assertEqual(states["PESD1-11276"], L.PROPOSED)    # answerable
            self.assertEqual(states["PESD1-11277"], L.ESCALATED)   # refund
            self.assertEqual(states["PESD1-11278"], L.ESCALATED)   # vague
            self.assertEqual(states["PESD1-11279"], L.PROPOSED)    # catalog bug

            by_ticket = {p.ticket: p for p in P.load_all()}
            self.assertEqual(by_ticket["PESD1-11276"].classification, "ANSWERABLE")
            self.assertIsNone(by_ticket["PESD1-11276"].clone)
            self.assertEqual(by_ticket["PESD1-11279"].classification, "NEEDS_CODE")
            self.assertEqual(by_ticket["PESD1-11279"].clone.target_project, "PRDT")
            self.assertEqual(by_ticket["PESD1-11279"].clone.assignee, "Pim Wattana")
            self.assertTrue(by_ticket["PESD1-11279"].clone.assignee_alternates)
            self.assertEqual(by_ticket["PESD1-11275"].clone.assignee, "Nadia Rahman")

            # every proposal validates
            for proposal in P.load_all():
                self.assertEqual(P.validate(proposal.to_dict()), [],
                                 f"{proposal.proposal_id} invalid")

    def test_clone_title_keeps_quoted_phrases_intact(self):
        """A title ending in a quoted name must not lose its closing quote."""
        from agents.jira_leader.analysis import _title
        self.assertEqual(_title('Add two new locations in NS - "A" and "B"'),
                         'Add two new locations in NS - "A" and "B"')
        self.assertEqual(_title('"Please add pricing labels"'),
                         "Please add pricing labels")
        self.assertEqual(_title("Hello, could you please remove the IR number"),
                         "Remove the IR number")

    def test_clone_description_has_the_required_structure(self):
        from agents.jira_leader import description as D
        with sandbox():
            build_corpus()
            run_queue()
            clone = next(p.clone for p in P.load_all() if p.clone)
            for heading in ("h2. Overview", "h2. Current result",
                            "h2. Expected outcome", "h2. Request details",
                            "h2. Original request (verbatim)",
                            "h2. Related history", "h2. Where to start"):
                self.assertIn(heading, clone.description, heading)
            self.assertIn("{quote}", clone.description)

    def test_a_screenshot_is_not_a_resolution_note(self):
        from agents.jira_leader.description import resolution_note
        self.assertEqual(resolution_note(
            {"body": "the request\n\n!image-20260914-090735.png|width=780!"}), "")
        self.assertEqual(resolution_note(
            {"body": "the request\n\n[~accountid:abc123] ok"}), "")
        self.assertEqual(
            resolution_note({"body": "x\n\nRemove Item Receipt in netsuite, then "
                                     "re-receive the order against the invoice."}),
            "Remove Item Receipt in netsuite, then re-receive the order against "
            "the invoice.")

    def test_entities_are_the_subject_not_the_verb(self):
        from agents.jira_leader.description import entities
        found = entities('Add two new locations in NS - "TH Central Si Racha" '
                         'and "TH Happitat", order 3161582')
        self.assertIn("TH Central Si Racha", found["names"])
        self.assertIn("TH Happitat", found["names"])
        self.assertIn("3161582", found["references"])
        self.assertNotIn("Add Two", found["names"])

    def test_never_touch_ticket_gets_no_comment_and_no_clone(self):
        with sandbox():
            build_corpus()
            run_queue()
            refund = next(p for p in P.load_all() if p.ticket == "PESD1-11277")
            self.assertEqual(refund.classification, "ESCALATE")
            self.assertEqual(refund.proposed_comment, "")
            self.assertIsNone(refund.clone)
            self.assertIsNone(refund.pesd1_transition)
            self.assertTrue(any(f.startswith("never_touch") for f in refund.flags))

    def test_second_run_reprocesses_nothing(self):
        """Invariant 4 across a whole run."""
        with sandbox():
            build_corpus()
            run_queue()
            again = run_queue()
            self.assertEqual(again["claimed"], 0)
            self.assertEqual(len(again["skipped"]), 6)

    def test_batch_apply_records_corrections_and_moves_state(self):
        with sandbox():
            build_corpus()
            run_queue()
            paths = batch_mod.assemble()
            batch_file = pathlib.Path(paths["json"])
            data = json.loads(batch_file.read_text())
            pending = [i for i in data["items"] if i["decision"] == "pending"]
            self.assertEqual(len(pending), 3)

            edited, approved, rejected = pending[0], pending[1], pending[2]
            edited["decision"] = "approve"
            edited["reason"] = "tone"
            edited["proposal"]["proposed_comment"] += " Thanks."
            approved["decision"] = "approve"
            rejected["decision"] = "reject"
            rejected["reason"] = "out of date"
            batch_file.write_text(json.dumps(data, indent=2))

            result = batch_mod.apply(paths["json"])
            self.assertEqual(len(result["approved"]), 1)
            self.assertEqual(len(result["corrected"]), 1)
            self.assertEqual(len(result["rejected"]), 1)
            self.assertEqual(result["errors"], [])

            from core import corrections
            logged = corrections.load()
            self.assertTrue(any(c["field"] == "proposed_comment" for c in logged))
            self.assertTrue(any(c["field"] == "__rejected__" for c in logged))

            with L.Ledger() as led:
                stats = led.stats()
            self.assertEqual(stats.get(L.APPROVED), 1)
            self.assertEqual(stats.get(L.CORRECTED), 1)
            self.assertEqual(stats.get(L.REJECTED), 1)

    def test_batch_execute_defaults_to_dry_run(self):
        with sandbox():
            build_corpus()
            run_queue()
            paths = batch_mod.assemble()
            batch_file = pathlib.Path(paths["json"])
            data = json.loads(batch_file.read_text())
            for item in data["items"]:
                if item["decision"] == "pending":
                    item["decision"] = "approve"
            batch_file.write_text(json.dumps(data, indent=2))
            batch_mod.apply(paths["json"])

            results = batch_mod.execute_approved()          # no --execute
            self.assertTrue(results)
            self.assertTrue(all(r["dry_run"] for r in results))
            with L.Ledger() as led:
                self.assertEqual(led.stats().get(L.EXECUTED), None)

    def test_edited_proposal_that_breaks_an_invariant_is_refused(self):
        with sandbox():
            build_corpus()
            run_queue()
            paths = batch_mod.assemble()
            batch_file = pathlib.Path(paths["json"])
            data = json.loads(batch_file.read_text())
            item = next(i for i in data["items"] if i["decision"] == "pending")
            item["decision"] = "approve"
            item["proposal"]["requester"] = {"email": "guess@pomelofashion.com",
                                             "source": "unknown",
                                             "confidence": "unknown",
                                             "matched_row": {}}
            batch_file.write_text(json.dumps(data, indent=2))
            result = batch_mod.apply(paths["json"])
            self.assertTrue(result["errors"])
            self.assertIn("invalid", result["errors"][0]["error"])

    def test_ticket_reopens_when_content_changes(self):
        with sandbox():
            build_corpus()
            run_queue()
            with L.Ledger() as led:
                row = led.get("PESD1-11276")
                ok, _ = led.should_process("PESD1-11276", "different-hash",
                                           config.boards()["intake"]["open_status"])
            self.assertEqual(row["state"], L.PROPOSED)
            self.assertFalse(ok, "a PROPOSED ticket is in flight, not re-processable")


if __name__ == "__main__":
    unittest.main()
