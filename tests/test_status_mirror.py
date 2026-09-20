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

    def __init__(self, statuses, offered=None):
        self.statuses = dict(statuses)
        self.offered = offered or {}
        self.moves = []

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
