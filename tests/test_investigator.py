"""The investigate flow: ids, the query catalog, the read-only guard, playbooks, packets."""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from agents.investigator import catalog, db, entities, playbooks
from tests.helpers import fixture_boards

TEMPLATES = {
    "return_by_id": "-- id: return_by_id\n-- db: spanky\n-- kind: read\n-- params: rma_ids:ints\n"
                    "SELECT * FROM pml_order_return WHERE id_order_return IN ({{rma_ids}})\n",
    "returns_on_orders": "-- id: returns_on_orders\n-- db: spanky\n-- kind: read\n-- params: order_ids:ints\n"
                         "SELECT * FROM pml_order_return WHERE id_order IN ({{order_ids}})\n",
    "delete_returns": "-- id: delete_returns\n-- title: Delete duplicate returns\n-- db: spanky\n-- kind: write\n"
                      "-- params: rma_ids:ints\nDELETE FROM pml_order_return WHERE id_order_return IN ({{rma_ids}});\n",
    "henry_po": "-- id: henry_po\n-- db: henry\n-- kind: read\n-- params: po_ids:ints\n"
                "SELECT * FROM `order` WHERE id_order IN ({{po_ids}})\n",
    "henry_po_set_status": "-- id: henry_po_set_status\n-- db: henry\n-- kind: write\n-- params: po:int, status:int\n"
                           "UPDATE `order` SET current_status = {{status}} WHERE id_order = {{po}};\n",
}

# PESD1-11391, as found on live Apollo on 12 Nov 2026.
RETURN_ROWS = [{"id_order_return": "1413955", "id_order": "3865970", "shop": "Zalora HK", "state_name": "Waiting For Package"}]
ORDER_ROWS = [
    {"id_order_return": "1413832", "id_order": "3865970", "state_name": "Return Received", "id_order_detail": "10844364", "slips": "1"},
    {"id_order_return": "1413955", "id_order": "3865970", "state_name": "Waiting For Package", "id_order_detail": "10844364", "slips": "0"},
]


def runner_for(answers: dict):
    def run(db_name, sql):
        for needle, rows in answers.items():
            if needle in sql:
                return rows
        return []
    return run


def make_run(templates: dict[str, catalog.Template], answers: dict):
    raw = runner_for(answers)
    return (lambda tid, **p: raw(templates[tid].db, templates[tid].render(**p)),
            lambda tid, **p: templates[tid].render(**p))


class Catalog:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "catalog").mkdir()
        for name, body in TEMPLATES.items():
            (root / "catalog" / f"{name}.sql").write_text(body)
        self.root = root

    def load(self):
        return catalog.load(self.root)


class EntityTests(unittest.TestCase):
    def test_ids_in_the_ways_requesters_write_them(self):
        e = entities.extract("Please delete RMA139 4747 and RMA1413955. PO 35505 "
                             "https://henry.pomelofashion.com/order/18387/view order 3865970 "
                             "https://henry.pomelofashion.com/product/508167/view TO-00964910 151164-381846")
        self.assertEqual(e.returns, [1394747, 1413955])
        self.assertEqual(e.pos, [35505, 18387])
        self.assertEqual(e.orders, [3865970])
        self.assertEqual(e.products, [508167])
        self.assertEqual(e.transfers, ["TO-00964910"])
        self.assertEqual(e.sku_refs, ["151164-381846"])

    def test_po_invoice_pairs_are_pos_not_skus(self):
        e = entities.extract("33920-55620,33943-55621: Both PO's currency should be RMB")
        self.assertEqual(e.pos, [33920, 33943])
        self.assertEqual(e.invoices, [55620, 55621])
        self.assertEqual(e.sku_refs, [])

    def test_from_x_to_y_means_the_ask_is_y(self):
        self.assertEqual(entities.extract("currency should updated from THB to RMB").currencies, ["RMB"])
        self.assertEqual(entities.extract("Adjust product 510465 to be RMB").currencies, ["RMB"])

    def test_a_bare_number_is_not_guessed(self):
        self.assertTrue(entities.extract("we sold 3865970 items").empty())

    def test_linked_sheet_is_noted_and_supplied_ids_merge(self):
        e = entities.extract("Henry issue on MC https://drive.google.com/file/d/1Z16/view?usp=drivesdk")
        self.assertEqual(e.links, ["https://drive.google.com/file/d/1Z16/view?usp=drivesdk"])
        self.assertEqual(e.merge({"pos": [18387, 18550], "nope": [1]}).pos, [18387, 18550])


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.c = Catalog()
        self.t = self.c.load()

    def tearDown(self):
        self.c.tmp.cleanup()

    def test_ints_render_and_injection_is_refused(self):
        self.assertIn("IN (1, 2)", self.t["return_by_id"].render(rma_ids=[1, 2]))
        with self.assertRaises(catalog.RenderError):
            self.t["return_by_id"].render(rma_ids=["1) OR 1=1 --"])

    def test_missing_param_is_refused(self):
        with self.assertRaises(catalog.RenderError):
            self.t["henry_po_set_status"].render(po=1)

    def test_strings_are_quoted_and_escaped(self):
        self.assertEqual(catalog._literal("str", "O'Brien", "x"), "'O''Brien'")

    def test_kind_is_required(self):
        with self.assertRaises(catalog.RenderError):
            catalog.parse("-- id: x\n-- db: spanky\nSELECT 1")


class ReadOnlyGuardTests(unittest.TestCase):
    def test_reads_pass(self):
        for sql in ("SELECT 1", "with x as (select 1) select * from x", "SHOW TABLES",
                    "SELECT REPLACE(name, 'a', 'b') FROM t", "SELECT * FROM t WHERE note = 'please delete'"):
            db.check_read_only(sql)

    def test_writes_and_tricks_are_refused(self):
        for sql in ("DELETE FROM t", "UPDATE t SET a=1", "SELECT 1; DROP TABLE t",
                    "SELECT * FROM t INTO OUTFILE '/tmp/x'", "SELECT * FROM t FOR UPDATE",
                    "INSERT INTO t VALUES (1)", "SET @a = 1", "", "/* hi */ DELETE FROM t"):
            with self.assertRaises(db.NotReadOnly, msg=sql):
                db.check_read_only(sql)

    def test_query_refuses_unknown_database(self):
        with self.assertRaises(db.NotReadOnly):
            db.query("mysql", "SELECT 1")


class PlaybookTests(unittest.TestCase):
    def setUp(self):
        self.c = Catalog()
        self.t = self.c.load()

    def tearDown(self):
        self.c.tmp.cleanup()

    def test_duplicate_return_keeps_the_refunded_one(self):
        run, render = make_run(self.t, {"id_order_return IN": RETURN_ROWS, "id_order IN": ORDER_ROWS})
        f = playbooks._duplicate_returns(entities.Entities(returns=[1413955]), run, render)
        self.assertIn("1413955", f.fix_sql)
        self.assertNotIn("1413832", f.fix_sql)
        self.assertEqual(f.confidence, "high")

    def test_a_return_with_a_refund_is_held_back(self):
        rows = [dict(r, slips="1") for r in ORDER_ROWS]
        run, render = make_run(self.t, {"id_order_return IN": RETURN_ROWS, "id_order IN": rows})
        f = playbooks._duplicate_returns(entities.Entities(returns=[1413955]), run, render)
        self.assertEqual(f.fix_sql, "")
        self.assertTrue(f.info_needed)

    def test_cancelled_po_gets_a_revert_script(self):
        run, render = make_run(self.t, {"FROM `order`": [{"po": "35505", "current_status": "4", "supplier": "X",
                                                          "id_merchandise_collection": "1", "date_updated": "x"}]})
        f = playbooks._po_status(entities.Entities(pos=[35505]), run, render)
        self.assertIn("current_status = 2 WHERE id_order = 35505", f.fix_sql)

    def _po(self, **over):
        row = {"po": "18541", "po_currency": "THB", "supplier": "S", "supplier_currency": "USD",
               "supplier_currency_id": "5", "line_currencies": "USD", "line_currency_ids": "5"}
        row.update(over)
        return row

    def test_po_currency_fix_only_when_supplier_lines_and_requester_agree(self):
        run, _ = make_run(self.t, {"FROM `order`": [self._po()]})
        render = lambda tid, **p: f"SET {p}"
        f = playbooks._po_currency(entities.Entities(pos=[18541], currencies=["USD"]), run, render)
        self.assertIn("'currency': 5", f.fix_sql)
        self.assertEqual(f.confidence, "high")

    def test_po_currency_disagreement_gets_no_fix(self):
        # PESD1-11388: requester asks for RMB, supplier pays THB — not safe to guess.
        run, render = make_run(self.t, {"FROM `order`": [self._po(supplier_currency="THB", line_currencies="THB")]})
        f = playbooks._po_currency(entities.Entities(pos=[18541], currencies=["RMB"]), run, render)
        self.assertEqual(f.fix_sql, "")
        self.assertTrue(any("disagree" in q for q in f.info_needed))

    def test_po_without_currency_gets_no_fix(self):
        run, render = make_run(self.t, {"FROM `order`": [self._po(po_currency=None)]})
        f = playbooks._po_currency(entities.Entities(pos=[18541]), run, render)
        self.assertEqual(f.fix_sql, "")
        self.assertEqual(f.confidence, "low")

    def test_money_tickets_get_no_fix(self):
        chosen = playbooks.choose("please refund the cashback on order 123456", entities.Entities(orders=[123456]))
        self.assertTrue(chosen[0].never_touch)
        f = chosen[0].investigate(entities.Entities(), lambda *a, **k: [], lambda *a, **k: "")
        self.assertEqual(f.fix_sql, "")

    def test_keywords_match_whole_words_only(self):
        # "po" inside "report"/"support", "back to" alone: not a PO status revert.
        text = "Please send the sales report back to support, it was cancelled"
        self.assertNotIn("po_status_revert", [p.key for p in playbooks.choose(text, entities.extract(text))])
        text = "Please revert PO 35505 from cancelled"
        self.assertEqual(playbooks.choose(text, entities.extract(text))[0].key, "po_status_revert")

    def test_duplicate_return_ticket_matches_its_playbook(self):
        text = "Please delete the return order as it has been created multiple times by the system. RMA1413955"
        self.assertEqual(playbooks.choose(text, entities.extract(text))[0].key, "duplicate_return")


class FlowTests(unittest.TestCase):
    def test_flow_is_read_only_and_human_triggered(self):
        from core.flow import Human, JIRA_WRITE
        from flows.investigate import INVESTIGATE
        self.assertTrue(all(isinstance(t, Human) for t in INVESTIGATE.triggers))
        self.assertFalse([s for s in INVESTIGATE.steps if getattr(s, "effect", None) == JIRA_WRITE])

    def test_end_to_end_packet(self):
        from flows.investigate import INVESTIGATE
        c = Catalog()
        out = tempfile.TemporaryDirectory()
        issue = {"key": "PESD1-11391", "fields": {"summary": "Please delete the return order as it has been created multiple times by the system.",
                                                  "description": "RMA1413955"}}
        old = os.environ.get("QUERIES_REPO")
        old_roots = os.environ.get("INVESTIGATE_CODE_ROOTS")
        os.environ["QUERIES_REPO"] = str(c.root)
        os.environ["INVESTIGATE_CODE_ROOTS"] = f"none={c.root / 'no-repo'}"
        try:
            with fixture_boards():
                INVESTIGATE.run(issues=[issue], output=pathlib.Path(out.name),
                                runner=runner_for({"id_order_return IN": RETURN_ROWS, "id_order IN": ORDER_ROWS}))
            text = (pathlib.Path(out.name) / "PESD1-11391.md").read_text()
        finally:
            if old is None:
                os.environ.pop("QUERIES_REPO", None)
            else:
                os.environ["QUERIES_REPO"] = old
            if old_roots is None:
                os.environ.pop("INVESTIGATE_CODE_ROOTS", None)
            else:
                os.environ["INVESTIGATE_CODE_ROOTS"] = old_roots
            c.tmp.cleanup()
            out.cleanup()
        self.assertIn("Duplicate return order", text)
        self.assertIn("DELETE FROM pml_order_return WHERE id_order_return IN (1413955)", text)
        self.assertIn("RMA1413832", text)


if __name__ == "__main__":
    unittest.main()
