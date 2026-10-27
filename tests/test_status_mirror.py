"""Mirroring a PRDT clone's status onto its PESD1 parent.

This is the one write that does not go through execute_proposal, so most of
these tests are about the limits of that exception rather than the happy path:
transitions only, forward only, never a close, never a board out of scope, and
never a transition the workflow does not offer.
"""
import unittest

from core import ledger as L, status_mirror as M
from tests.helpers import sandbox


class FakeJira:
    """Read and write in one stub; `moves` records every transition attempted."""

    def __init__(self, statuses, offered=None, links=None):
        self.statuses = dict(statuses)
        self.offered = offered or {}
        self.links = links or {}          # parent -> [linked keys] (Cloners)
        self.moves = []
        self.searches = []

    def search(self, jql, fields=None, **kw):
        self.searches.append(jql)
        if self.links is None:
            raise RuntimeError("search down")
        return [{"key": parent, "fields": {"issuelinks": [
                    {"type": {"name": "Cloners"}, "inwardIssue": {"key": k}}
                    for k in keys]}}
                for parent, keys in self.links.items()]

    def issue(self, key, fields="*all"):
        return {"fields": {"status": {"name": self.statuses[key]}}}

    def transitions(self, key):
        names = self.offered.get(key)
        if names is None:               # default: the whole ladder is reachable
            names = list(M.ORDER) + ["Blocked", "Closed - Won't Do"]
        return [{"name": f"To {n}", "to": {"name": n}} for n in names]

    def transition(self, key, status_name, fields=None):
        self.moves.append((key, status_name))
        self.statuses[key] = status_name
        return {"ok": True}


def executed(led, parent="PESD1-11282", clone="PRDT-11570"):
    """A parent whose clone is recorded — the only shape mirroring looks at."""
    led.claim(parent, "hash")
    led.transition(parent, L.PROPOSED, proposal_id="p_test")
    led.transition(parent, L.APPROVED)
    led.transition(parent, L.EXECUTED, clone_key=clone)


class MappingTests(unittest.TestCase):
    def test_statuses_present_on_both_boards_map_to_themselves(self):
        for name in ("To Do", "In progress", "Ready For Code Review",
                     "Ready To Release", "Live", "Blocked"):
            self.assertEqual(M.target_for(name), name)

    def test_ready_for_qa_reads_as_in_progress(self):
        """PESD1 has no QA status; a clone in QA is still in flight."""
        self.assertEqual(M.target_for("Ready For QA"), "In progress")

    def test_unassigned_reads_as_to_do(self):
        self.assertEqual(M.target_for("Unassigned"), "To Do")

    def test_a_closing_status_is_never_mirrored(self):
        """Invariant 9: closing stays a human decision."""
        self.assertIsNone(M.target_for("Closed - Won't Do"))

    def test_an_unknown_status_is_not_guessed(self):
        self.assertIsNone(M.target_for("Awaiting Interstellar Review"))


class DirectionTests(unittest.TestCase):
    def test_forward_moves_are_allowed(self):
        self.assertTrue(M.is_forward("To Do", "In progress"))
        self.assertTrue(M.is_forward("Waiting Support", "Live"))

    def test_a_parent_is_never_dragged_backwards(self):
        self.assertFalse(M.is_forward("Live", "In progress"))
        self.assertFalse(M.is_forward("In progress", "To Do"))

    def test_standing_still_is_not_a_move(self):
        self.assertFalse(M.is_forward("To Do", "To Do"))

    def test_blocked_can_be_entered_from_anywhere_and_left(self):
        self.assertTrue(M.is_forward("In progress", "Blocked"))
        self.assertTrue(M.is_forward("Blocked", "To Do"))
        self.assertFalse(M.is_forward("Blocked", "Blocked"))


class DriftTests(unittest.TestCase):
    def test_a_moved_clone_is_reported_as_drift(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do", "PRDT-11570": "In progress"})
                row = M.drift(j, led)[0]
            self.assertEqual(row["action"], "mirror")
            self.assertEqual(row["target"], "In progress")

    def test_matching_statuses_need_nothing(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "Live", "PRDT-11570": "Live"})
                self.assertEqual(M.drift(j, led)[0]["action"], "none")

    def test_a_closed_clone_is_reported_not_mirrored(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do",
                              "PRDT-11570": "Closed - Won't Do"})
                row = M.drift(j, led)[0]
            self.assertEqual(row["action"], "report")
            self.assertIn("human decision", row["why"])

    def test_a_backwards_move_is_reported_not_made(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "Live", "PRDT-11570": "To Do"})
                row = M.drift(j, led)[0]
            self.assertEqual(row["action"], "report")
            self.assertIn("backwards", row["why"])

    def test_a_clone_outside_scope_is_ignored(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led, clone="BUSK-2805")
                j = FakeJira({"PESD1-11282": "To Do", "BUSK-2805": "Live"})
                self.assertEqual(M.drift(j, led), [])


class LinkedCloneTests(unittest.TestCase):
    """Pairs found from Jira clone links, not just the ledger (27 Oct 2026)."""

    def test_a_clone_linked_in_jira_is_mirrored_without_a_ledger_row(self):
        with sandbox():
            with L.Ledger() as led:
                j = FakeJira({"PESD1-10339": "To Do", "PRDT-10713": "Live"},
                             links={"PESD1-10339": ["PRDT-10713"]})
                rows = M.drift(j, led)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["parent"], rows[0]["action"], rows[0]["target"]),
                             ("PESD1-10339", "mirror", "Live"))

    def test_the_search_only_asks_for_open_pesd1_parents(self):
        with sandbox():
            with L.Ledger() as led:
                j = FakeJira({})
                M.drift(j, led)
            self.assertIn("project = PESD1", j.searches[0])
            self.assertIn("Cloners", j.searches[0])

    def test_a_link_to_another_board_is_ignored(self):
        with sandbox():
            with L.Ledger() as led:
                j = FakeJira({"PESD1-1": "To Do", "BUSK-9": "Live"},
                             links={"PESD1-1": ["BUSK-9"]})
                self.assertEqual(M.drift(j, led), [])

    def test_clones_that_agree_move_the_parent(self):
        with sandbox():
            with L.Ledger() as led:
                j = FakeJira({"PESD1-1": "To Do", "PRDT-1": "Live", "PRDT-2": "Live"},
                             links={"PESD1-1": ["PRDT-1", "PRDT-2"]})
                row = M.drift(j, led)[0]
            self.assertEqual((row["action"], row["target"]), ("mirror", "Live"))

    def test_clones_that_disagree_are_reported_not_mirrored(self):
        with sandbox():
            with L.Ledger() as led:
                j = FakeJira({"PESD1-1283": "To Do", "PRDT-7251": "Blocked",
                              "PRDT-7511": "To Do"},
                             links={"PESD1-1283": ["PRDT-7251", "PRDT-7511"]})
                row = M.drift(j, led)[0]
                moved = M.apply_items([row], j, j, led, execute=True)
            self.assertEqual(row["action"], "report")
            self.assertIn("clones disagree", row["why"])
            self.assertEqual(j.moves, [])
            self.assertEqual(moved, [])

    def test_a_pair_in_both_the_ledger_and_jira_is_seen_once(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do", "PRDT-11570": "Live"},
                             links={"PESD1-11282": ["PRDT-11570"]})
                rows = M.drift(j, led)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["clone"], "PRDT-11570")

    def test_a_failed_link_search_still_mirrors_the_ledger(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do", "PRDT-11570": "Live"})
                j.links = None
                rows = M.drift(j, led)
            self.assertEqual([r["parent"] for r in rows], ["PESD1-11282"])

    def test_linked_parents_keep_every_other_limit(self):
        with sandbox():
            with L.Ledger() as led:
                j = FakeJira({"PESD1-1": "Live", "PRDT-1": "To Do",
                              "PESD1-2": "To Do", "PRDT-2": "Closed - Won't Do"},
                             links={"PESD1-1": ["PRDT-1"], "PESD1-2": ["PRDT-2"]})
                rows = {r["parent"]: r for r in M.drift(j, led)}
            self.assertEqual(rows["PESD1-1"]["action"], "report")   # backwards
            self.assertEqual(rows["PESD1-2"]["action"], "report")   # closing


class ApplyTests(unittest.TestCase):
    def test_a_dry_run_writes_nothing(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do", "PRDT-11570": "Live"})
                out = M.apply(j, j, led, execute=False)
            self.assertEqual(j.moves, [])
            self.assertFalse(out[0]["executed"])

    def test_executing_moves_the_parent_and_records_where_from(self):
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do", "PRDT-11570": "Live"})
                out = M.apply(j, j, led, execute=True)
            self.assertEqual(j.moves, [("PESD1-11282", "Live")])
            self.assertEqual(out[0]["from_status"], "To Do")

    def test_a_transition_the_workflow_does_not_offer_is_reported(self):
        """From Waiting Support, PESD1 cannot reach In progress directly."""
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira(
                    {"PESD1-11282": "Waiting Support", "PRDT-11570": "In progress"},
                    offered={"PESD1-11282": ["To Do", "Blocked", "Live"]})
                out = M.apply(j, j, led, execute=True)
            self.assertEqual(j.moves, [])
            self.assertEqual(out[0]["action"], "report")
            self.assertIn("cannot reach", out[0]["why"])

    def test_transition_is_the_only_writer_method_called(self):
        """The exception is transitions only — no comment, clone, link, assign.

        Asserted on the call sites rather than on words, so "Unassigned" in the
        status map does not read as an assign call.
        """
        import pathlib
        import re
        text = pathlib.Path(M.__file__).read_text(encoding="utf-8")
        called = set(re.findall(r"writer\.(\w+)\(", text))
        self.assertEqual(called, {"transition"}, called)

    def test_the_writer_is_only_ever_given_a_parent_key(self):
        """A mirror must never transition the clone, only the PESD1 parent."""
        with sandbox():
            with L.Ledger() as led:
                executed(led)
                j = FakeJira({"PESD1-11282": "To Do", "PRDT-11570": "Live"})
                M.apply(j, j, led, execute=True)
            self.assertTrue(all(k.startswith("PESD1-") for k, _ in j.moves), j.moves)


if __name__ == "__main__":
    unittest.main()
