"""Board ranking: priority order, stable within a priority, reversible."""
import unittest

from core import board_rank as R
from core import jira_client as J
from tests.helpers import sandbox


class FakeBoard:
    """Applies rank calls to an in-memory list, like Jira's rank endpoint."""

    def __init__(self, keys):
        self.order = list(keys)
        self.calls = []

    def rank_issues(self, keys, *, before=None, after=None, rank_field=None):
        self.calls.append({"keys": list(keys), "before": before, "after": after})
        for k in keys:
            self.order.remove(k)
        anchor = self.order.index(before or after) + (0 if before else 1)
        self.order[anchor:anchor] = list(keys)
        return {}


def rows(*pairs):
    return [{"key": k, "priority": p} for k, p in pairs]


BOARD = rows(("PRDT-1", "Medium"), ("PRDT-2", "Critical"), ("PRDT-3", "Low"),
             ("PRDT-4", "High"), ("PRDT-5", "Critical"), ("PRDT-6", None),
             ("PRDT-7", "Medium"))


class BoardRankTests(unittest.TestCase):
    def test_priority_order_keeps_existing_order_within_a_priority(self):
        self.assertEqual(R.target_order(BOARD),
                         ["PRDT-2", "PRDT-5", "PRDT-4", "PRDT-1", "PRDT-7",
                          "PRDT-3", "PRDT-6"])

    def test_apply_lays_the_board_out_in_target_order(self):
        with sandbox():
            board = FakeBoard([r["key"] for r in BOARD])
            result = R.apply(347, execute=True, rows=BOARD, writer=board)
            self.assertTrue(result["applied"])
            self.assertEqual(board.order, R.target_order(BOARD))

    def test_large_boards_move_in_batches_of_fifty(self):
        big = rows(*[(f"PRDT-{i}", ["Low", "Critical", "High"][i % 3])
                     for i in range(1, 131)])
        with sandbox():
            board = FakeBoard([r["key"] for r in big])
            R.apply(347, execute=True, rows=big, writer=board)
            self.assertEqual(board.order, R.target_order(big))
            self.assertTrue(all(len(c["keys"]) <= 50 for c in board.calls))

    def test_undo_restores_the_prior_order(self):
        with sandbox():
            original = [r["key"] for r in BOARD]
            board = FakeBoard(original)
            run = R.apply(347, execute=True, rows=BOARD, writer=board)
            R.undo(run["run_id"], execute=True, writer=board)
            self.assertEqual(board.order, original)

    def test_a_board_already_in_order_is_left_alone(self):
        ordered = rows(("PRDT-2", "Critical"), ("PRDT-4", "High"), ("PRDT-1", "Low"))
        with sandbox():
            board = FakeBoard([r["key"] for r in ordered])
            result = R.apply(347, execute=True, rows=ordered, writer=board)
            self.assertFalse(result["applied"])
            self.assertEqual(board.calls, [])

    def test_dry_run_records_nothing_and_writes_nothing(self):
        with sandbox():
            writer = J.JiraWriteClient(base_url="https://x", email="e", token="t")
            result = R.apply(347, rows=BOARD, writer=writer)
            self.assertFalse(result["applied"])
            self.assertEqual(writer.performed, [])
            self.assertFalse(R._history_path().exists())

    def test_rank_write_is_scoped_and_bounded(self):
        writer = J.JiraWriteClient(base_url="https://x", email="e", token="t")
        with self.assertRaises(J.ScopeError):
            writer.rank_issues(["HENRY-1"], after="PRDT-1")
        with self.assertRaises(ValueError):
            writer.rank_issues([f"PRDT-{i}" for i in range(51)], after="PRDT-99")
        with self.assertRaises(ValueError):
            writer.rank_issues(["PRDT-1"], before="PRDT-2", after="PRDT-3")


if __name__ == "__main__":
    unittest.main()
