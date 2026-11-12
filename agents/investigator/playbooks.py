"""Playbooks — the issue types we have solved before, and how to solve them again.

Each playbook says: which words identify it, which ids it needs, which catalogued
read queries to run, how to turn their rows into a root cause, and the step-by-step
fix (with any fix SQL rendered from a `write` template, for a person to run).

They are built from real PESD1 tickets — the precedent is named on each. When a ticket
matches none, the packet still lists its ids, code hits and the questions to ask; a new
playbook (and its queries in the Queries repo) is added once a person has solved it.

`run(template_id, **params)` returns rows or raises db.DatabaseUnavailable;
`render(template_id, **params)` returns the SQL text of a `write` template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from agents.investigator.entities import Entities

PO_STATUS = {"1": "Open", "2": "Ordered", "3": "Production fully completed", "4": "Cancelled",
             "5": "Product partially completed", "6": "Price confirmed"}


@dataclass
class Finding:
    root_cause: str = ""
    evidence: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    fix_sql: str = ""
    info_needed: list[str] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    confidence: str = "low"       # low | medium | high


@dataclass
class Playbook:
    key: str
    title: str
    all_of: list[list[str]]       # every group must have at least one word in the text
    needs: str                    # the Entities field this playbook works from
    code_terms: list[str]
    precedent: str
    investigate: Callable[[Entities, Callable, Callable], Finding]
    never_touch: bool = False
    code_repos: tuple[str, ...] = ()   # where code_terms are searched; empty = every clone

    def matches(self, text: str) -> int:
        """Whole words only — "po" must not match inside "report" or "support" (12 Nov triage)."""
        low = text.lower()
        score = 0
        for group in self.all_of:
            hits = sum(1 for w in group if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low))
            if not hits:
                return 0
            score += hits
        return score


# -- duplicate returns -------------------------------------------------------
def _duplicate_returns(ents: Entities, run, render) -> Finding:
    f = Finding()
    asked = ents.returns
    rows = run("return_by_id", rma_ids=asked)
    f.queries_run.append("return_by_id")
    found = {int(r["id_order_return"]): r for r in rows}
    gone = [r for r in asked if r not in found]
    if gone:
        f.evidence.append("Not in this database: " + ", ".join(f"RMA{r}" for r in gone)
                          + " (already deleted — or newer than this copy, if it is pre-production)")
    if not found:
        f.root_cause = "None of the listed returns are in this database."
        f.steps = ["If the evidence is from production: check Superset `online_sales` (ds 33); if it is clean too, reply that the returns were already removed and the requester should refresh.",
                   "If it is from pre-production: re-run on production (or check the return in Apollo admin) before concluding anything."]
        f.confidence = "low"
        return f
    orders = sorted({int(r["id_order"]) for r in found.values()})
    lines = run("returns_on_orders", order_ids=orders)
    f.queries_run.append("returns_on_orders")
    by_return: dict[int, dict] = {}
    for ln in lines:
        rid = int(ln["id_order_return"])
        e = by_return.setdefault(rid, {"order": int(ln["id_order"]), "state": ln.get("state_name"),
                                       "lines": set(), "slips": int(ln.get("slips") or 0)})
        if ln.get("id_order_detail"):
            e["lines"].add(int(ln["id_order_detail"]))
    delete, hold = [], []
    for rid in asked:
        if rid not in found:
            continue
        me = by_return.get(rid, {"order": int(found[rid]["id_order"]), "lines": set(), "slips": 0,
                                 "state": found[rid].get("state_name")})
        shop = found[rid].get("shop") or found[rid].get("id_shop")
        siblings = [o for o, e in by_return.items() if o != rid and e["order"] == me["order"]]
        covering = [o for o in siblings if me["lines"] and me["lines"] <= by_return[o]["lines"]]
        others = ", ".join("RMA%s (%s, slips %s)" % (o, by_return[o]["state"], by_return[o]["slips"])
                           for o in siblings) or "none"
        f.evidence.append(
            f"RMA{rid}: order {me['order']} ({shop}), state {me['state']}, lines "
            f"{sorted(me['lines']) or 'none (half-deleted)'}, credit slips {me['slips']}; "
            f"other returns on the order: {others}")
        if me["slips"]:
            hold.append(f"RMA{rid} has a credit slip (refund) — not a plain duplicate")
        elif not me["lines"]:
            delete.append(rid)  # half-deleted: finish the job
        elif covering:
            delete.append(rid)
        else:
            hold.append(f"RMA{rid} covers an item no other return covers — it may be the real return")
    f.root_cause = ("The return was created more than once for the same order lines; "
                    f"{len(delete)} of {len(found)} listed returns are plain duplicates.")
    if delete:
        f.fix_sql = render("delete_returns", rma_ids=delete)
        f.steps += [f"Run the script below on spanky (writer endpoint, write login): it removes {', '.join(f'RMA{r}' for r in delete)} — SELECTs first, then DELETEs in history → detail → return order.",
                    "NetSuite: ask Vishal to delete the same RMA numbers there (they sync separately).",
                    "Superset clears itself once spanky is cleaned. Reply \"Completed\" on the ticket."]
    if hold:
        f.info_needed += hold
        f.steps.append("Held back: " + "; ".join(hold) + ". Ask the requester which one is wrong.")
    f.confidence = "high" if delete and not hold else "medium"
    return f


# -- Henry PO status revert ---------------------------------------------------
def _po_status(ents: Entities, run, render) -> Finding:
    f = Finding()
    rows = run("henry_po", po_ids=ents.pos)
    f.queries_run.append("henry_po")
    for r in rows:
        f.evidence.append(f"PO {r['po']}: status {r['current_status']} ({PO_STATUS.get(r['current_status'], '?')}), "
                          f"supplier {r['supplier']}, MC {r['id_merchandise_collection']}, updated {r['date_updated']}")
    cancelled = [int(r["po"]) for r in rows if r["current_status"] == "4"]
    f.root_cause = ("Cancelled is a final status in Henry — the PO screen offers no way back "
                    "(app/OrderStatus.php getAvailableNextStates), so a revert is a data fix.")
    if cancelled:
        for po in cancelled:
            f.fix_sql += render("henry_po_set_status", po=po, status=2) + "\n\n"
        f.steps = ["Confirm the status the PO had before it was cancelled (Ordered = 2 is the usual 'In production'); change the status in the script if not.",
                   "Run the script on henry (write login). Cancelling also ran checkAndUpdateReleaseCollection — check the PO's release collection still lists it.",
                   "If the cancellation reached NetSuite, ask Vishal to reopen the NetSuite PO.",
                   "Reply \"Completed\" with a screenshot of the PO."]
        f.confidence = "medium"
    else:
        f.info_needed.append("None of the POs is Cancelled — ask the requester what status they see and want.")
    return f


# -- Henry PO styles (lines) cancelled -----------------------------------------
LINE_STATUS = {"1": "New", "3": "Canceled", "4": "Approved", "5": "Invoice Generated", "7": "Complete",
               "12": "Order Placed", "13": "In Production", "14": "Production Complete"}


def _po_lines(ents: Entities, run, render) -> Finding:
    f = Finding()
    if not ents.products:
        f.info_needed.append("Which styles? The ticket names the PO but no style ids — ask the requester.")
        return f
    rows = run("henry_po_lines", po_ids=ents.pos)
    f.queries_run.append("henry_po_lines")
    asked = set(ents.products)
    for r in rows:
        if int(r["style"]) in asked:
            f.evidence.append(f"PO {r['po']} style {r['style']}: line status {r['line_status']} "
                              f"({LINE_STATUS.get(str(r['line_status']), '?')}), {r['sizes']} sizes, qty {r['qty']}; "
                              f"PO total_quantity {r['po_total_quantity']}")
    cancelled = {int(r["style"]): int(r["po"]) for r in rows if int(r["style"]) in asked and str(r["line_status"]) == "3"}
    missing = sorted(asked - {int(r["style"]) for r in rows})
    if missing:
        f.info_needed.append(f"Styles {', '.join(map(str, missing))} are not on POs {', '.join(map(str, ents.pos))} in this database.")
    f.root_cause = ("Each style on a Henry PO has its own line status (order_detail.id_order_detail_status, 3 = Canceled). "
                    "Henry has no screen to undo a cancelled line — the PO shows it as a label and bulk status updates "
                    "skip it — so a revert is a data fix. Cancelling from the PO screen also took the style's quantity "
                    "out of order.total_quantity.")
    if cancelled:
        for po in sorted(set(cancelled.values())):
            styles = [s for s, p in cancelled.items() if p == po]
            f.fix_sql += render("henry_po_lines_set_status", po=po, products=styles, status=14) + "\n\n"
        f.steps = ["Check on the live PO that the styles were really produced (shipped quantity, invoices) — 14 Production Complete is what was asked; change the status in the script if not.",
                   "Run the script on henry with a write login (Unni). It only changes lines still Canceled and recounts the PO quantity.",
                   "Reopen the PO in Henry: the styles show Production Complete and the PO total includes them.",
                   "Check the release collection and NetSuite PO still carry the styles (cancelled lines are skipped by release collections).",
                   "Reply \"Completed\" with a screenshot of the PO."]
        f.confidence = "medium"
    elif rows:
        f.info_needed.append("None of the named styles is Canceled on this PO — ask the requester what status they see.")
    return f


# -- Henry PO size quantity: screen vs PO PDF ------------------------------------
def _po_size_qty(ents: Entities, run, render) -> Finding:
    f = Finding()
    rows = run("henry_po_size_channels", po_ids=ents.pos)
    f.queries_run.append("henry_po_size_channels")
    bad = [r for r in rows if int(r["screen_qty"] or 0) != int(r["pdf_qty"] or 0)]
    for r in bad:
        f.evidence.append(f"PO {r['po']} style {r['style']} {r['size']}: screen {r['screen_qty']}, PDF {r['pdf_qty']} "
                          f"(MC split TH {r['mc_th']}, ID {r['mc_id']}, MY {r['mc_my']}, samples "
                          f"{r['mc_sample_th']}/{r['mc_sample_id']}/{r['mc_sample_sg']}/{r['mc_sample_my']})")
    f.root_cause = ("The PO screen shows order_detail.qty_requested, but the PO PDF the supplier receives prints the sum of "
                    "the size's channel columns (qty_requested_th/_id/_my, qty_sample_*, order_detail_retail) and never "
                    "reads qty_requested (OrderController@exportOrderDetails). Sizes whose channel split is empty print 0. "
                    "PO edit paths can write qty_requested without the split (OrderService updates, approve-new-quantity).")
    if bad:
        f.steps = ["For each size listed, take the channel split from the MC (mc_* columns above); if the MC has none, ask the requester which channel the units are for.",
                   "Unni fills the size's order_detail channel columns (and order_detail_retail for retail) so they add up to the screen quantity.",
                   "Download the PO PDF again and check every size matches the screen, then send it to the supplier.",
                   "Raise a PRDT bug for Unni: the PO edit paths must keep qty_requested and the channel split in step."]
        f.confidence = "medium"
    elif rows:
        f.root_cause = "Every size's screen quantity matches the PDF channel split in this database."
        f.info_needed.append("Ask the requester which sizes are wrong and where they see it (screen, PDF, CSV).")
    return f


# -- Henry PO currency ---------------------------------------------------------
def _po_currency(ents: Entities, run, render) -> Finding:
    f = Finding()
    rows = run("henry_po", po_ids=ents.pos)
    f.queries_run.append("henry_po")
    asked = ents.currencies
    fixable: dict[str, list[int]] = {}
    unclear = []
    found = {int(r["po"]) for r in rows}
    for po in ents.pos:
        if po not in found:
            unclear.append(f"PO {po} is not in this database (newer than a pre-production copy?)")
    for r in rows:
        lines = [c for c in (r.get("line_currencies") or "").split(",") if c]
        f.evidence.append(f"PO {r['po']}: PO currency {r['po_currency'] or 'none'}, supplier {r['supplier']} "
                          f"pays in {r['supplier_currency']}, cost lines in {', '.join(lines) or 'none'}"
                          + (f"; requester asks for {', '.join(asked)}" if asked else ""))
        # Every source we have must point at the same currency before a fix is proposed.
        votes = {r["supplier_currency"]} | set(lines) | set(asked)
        votes.discard(None)
        if not r["po_currency"]:
            unclear.append(f"PO {r['po']} has no currency set — check it in Henry before changing anything")
        elif len(votes) != 1:
            unclear.append(f"PO {r['po']}: supplier, cost lines and requester disagree ({', '.join(sorted(votes))}) — "
                           "decide which is right (the product cost currency may be what is wrong, not the PO)")
        elif r["po_currency"] not in votes:
            target = next(iter(votes))
            cur_id = r["supplier_currency_id"] if target == r["supplier_currency"] else (r.get("line_currency_ids") or "").split(",")[0]
            fixable.setdefault(cur_id, []).append(int(r["po"]))
    f.root_cause = ("A PO's currency must match its supplier and its cost lines. order.id_currency defaults to THB "
                    "and is not updated when the supplier changes (Henry OrderService::updateOrder; PRDT-12009); "
                    "a product whose cost was entered in another currency causes the same 'currency mismatch'.")
    for cur, pos in fixable.items():
        f.fix_sql += render("henry_po_set_currency", po_ids=pos, currency=int(cur)) + "\n\n"
    if fixable:
        f.steps = ["Check no invoice has been created or synced to NetSuite for these POs (invoice totals would also need correcting).",
                   "Run the script on henry (write login), then reopen each PO in Henry and check the cost column."]
    f.info_needed += unclear
    if not fixable and not unclear:
        f.info_needed.append("Every PO already matches its supplier and cost lines — ask the requester for the SKU and screen showing the mismatch.")
    f.confidence = "high" if fixable and not unclear else ("medium" if fixable else "low")
    return f


# -- product visibility / activation ---------------------------------------------
def _sp_ids(ents: Entities, run, f: Finding) -> list[int]:
    rows = run("henry_products", product_ids=ents.products)
    f.queries_run.append("henry_products")
    sp = [int(r["sp_id_product"]) for r in rows if r.get("sp_id_product")]
    for r in rows:
        f.evidence.append(f"Henry {r['id_product']} → Apollo {r['sp_id_product'] or '(not synced)'}: {r['name'] or '(no name)'}")
    return sp or ents.products   # ids that aren't Henry ids may already be Apollo ids


def _visibility(ents: Entities, run, render) -> Finding:
    f = Finding()
    sp = _sp_ids(ents, run, f)
    rows = run("apollo_product_shops", sp_ids=sp)
    f.queries_run.append("apollo_product_shops")
    problems = []
    for r in rows:
        why = []
        if r["active"] != "1":
            why.append("inactive")
        if not r.get("release_date") or r["release_date"].startswith("0000"):
            why.append("no release date")
        if r.get("price") in (None, "0", "0.000000"):
            why.append("price 0")
        if why:
            problems.append(f"{r['id_product']} in {r['shop']}: {', '.join(why)}")
    f.evidence += problems or ["Every shop row is active, priced and released."]
    f.root_cause = ("Apollo shows a product only when it is active, priced above 0 and released in that shop "
                    "(Henry writes the release date per shop; including_global falls back to TH).")
    f.steps = ["For each problem above: set the release date in Henry (release collection) and sync, or activate it in Apollo if the date is right.",
               "Activation needs the 'activate product' permission, price > 0 and a release date that is not in the future."]
    f.confidence = "medium" if problems else "low"
    return f


def _measurement(ents: Entities, run, render) -> Finding:
    f = Finding()
    rows = run("henry_products", product_ids=ents.products)
    f.queries_run.append("henry_products")
    sp = [int(r["sp_id_product"]) for r in rows if r.get("sp_id_product")]
    apollo = {r["id_product"]: r for r in run("apollo_product_shops", sp_ids=sp)} if sp else {}
    f.queries_run.append("apollo_product_shops")
    for r in rows:
        a = apollo.get(r["sp_id_product"] or "", {})
        f.evidence.append(f"Henry {r['id_product']}: {r['filename'] or 'no image'} (master style {r['ms_file'] or 'none'}); "
                          f"Apollo {r['sp_id_product']}: {a.get('mi_file') or 'no image'}")
    f.root_cause = "Henry product.id_measurement_image syncs to Apollo pml_product.id_measurement_image; a gap on either side shows here."
    f.steps = ["Set the image on Henry's Measurement tab (product → Measurement) and press Update; the product sync carries it to Apollo."]
    return f


def _stock(ents: Entities, run, render) -> Finding:
    f = Finding()
    sp = _sp_ids(ents, run, f)
    for r in run("apollo_stock", sp_ids=sp):
        f.evidence.append(f"Apollo {r['id_product']} shop {r['id_shop']} {r['size']}: {r['quantity']}")
    f.queries_run.append("apollo_stock")
    f.root_cause = "Stock in Apollo comes from NetSuite (retail inventory sync) per shop; check the NetSuite location if it is wrong."
    f.steps = ["Compare with NetSuite on-hand for the same SKU and location; a difference is a sync problem for Vishal."]
    return f


def _order_state(ents: Entities, run, render) -> Finding:
    f = Finding()
    for r in run("order_with_history", order_ids=ents.orders):
        f.evidence.append(f"Order {r['id_order']} ({r['shop']}): {r['state_name']} at {r['date_add']} by employee {r['id_employee']}")
    f.queries_run.append("order_with_history")
    f.root_cause = "Order status history shows where the order stopped; the last row is the current state."
    f.steps = ["Read the history above; a missing step (e.g. never Shipped) points at the carrier or WMS sync."]
    return f


def _money(ents: Entities, run, render) -> Finding:
    f = Finding()
    if ents.orders:
        for r in run("order_with_history", order_ids=ents.orders):
            f.evidence.append(f"Order {r['id_order']}: {r['state_name']} at {r['date_add']}")
        for r in run("slips_on_orders", order_ids=ents.orders):
            f.evidence.append(f"Credit slip {r['id_order_slip']} on order {r['id_order']}: {r['amount']} (refunded {r['refunded']})")
        f.queries_run += ["order_with_history", "slips_on_orders"]
    f.root_cause = "Money ticket (refund / cashback / credit) — never-touch: evidence only, a human decides."
    f.steps = ["Escalate to Quenton with the evidence above. No fix is proposed for never-touch categories."]
    f.info_needed.append("Human decision required (CLAUDE.md invariant 3).")
    return f


PLAYBOOKS: list[Playbook] = [
    Playbook("duplicate_return", "Duplicate return order",
             [["return", "rma"], ["delete", "duplicate", "multiple", "twice", "incorrectly"]],
             "returns", ["pml_order_return", "createReturn", "AdminReturnController"],
             "PESD1-11281, 11305, 11363, 11391", _duplicate_returns, code_repos=("apollo",)),
    Playbook("po_line_revert", "Revert cancelled styles on a Henry PO",
             [["style", "styles", "item", "items"], ["revert", "cancelled", "canceled", "back to"]],
             "pos", ["ID_ORDER_DETAIL_STATUS_CANCELLED", "cancelOrderDetails"],
             "PESD1-11397", _po_lines, code_repos=("henry",)),
    Playbook("po_status_revert", "Revert a Henry PO status",
             [["po", "purchase order"], ["revert", "cancelled", "canceled", "back to"]],
             "pos", ["ID_STATUS_CANCELLED", "getAvailableNextStates", "checkAndUpdateReleaseCollection"],
             "PESD1-11064, 11389", _po_status, code_repos=("henry",)),
    Playbook("po_currency", "Henry PO currency does not follow the supplier",
             [["currency", "rmb", "thb", "usd"], ["po", "purchase order", "mc", "order"]],
             "pos", ["id_currency", "new_currency_id", "updateOrder"],
             "PESD1-11380/81/82, 11388 (PRDT-12009)", _po_currency, code_repos=("henry",)),
    Playbook("measurement_image", "Measurement image missing or wrong",
             [["measurement", "size guide", "size chart"]],
             "products", ["id_measurement_image", "measurement_guide"],
             "11 Nov PH check (33 products)", _measurement, code_repos=("henry", "apollo")),
    Playbook("product_visibility", "Product not showing / cannot be activated",
             [["not showing", "not visible", "can't see", "cannot see", "activate", "activation", "hidden", "not live"]],
             "products", ["release_date", "pml_product_activate_permission", "including_global"],
             "Apollo activation rules (6 Nov)", _visibility, code_repos=("apollo", "henry")),
    Playbook("po_size_qty", "Henry PO size quantity wrong or 0",
             [["qty", "quantity"], ["po", "purchase order", "mc"], ["xxs", "xs", "s", "m", "l", "xl", "xxl", "size", "missing", "0"]],
             "pos", ["exportOrderDetails", "setNormalMainEntityOrderData", "qty_requested_th"],
             "PESD1-11395", _po_size_qty, code_repos=("henry",)),
    Playbook("stock", "Stock / quantity looks wrong",
             [["stock", "quantity", "qty", "inventory"]],
             "products", ["pml_stock_available", "StockAvailable"],
             "11 Nov PH stock check", _stock, code_repos=("apollo", "api")),
    Playbook("money", "Refund / cashback / credit (never-touch)",
             [["refund", "cashback", "cash back", "credit", "perks", "raf", "payment"]],
             "orders", ["pml_order_slip", "pml_raf_credit_log"],
             "never_touch.yml", _money, never_touch=True, code_repos=("apollo", "api")),
    Playbook("order_state", "Order stuck or in the wrong status",
             [["order"], ["status", "stuck", "state", "not delivered", "not shipped"]],
             "orders", ["pml_order_history", "OrderHistory"],
             "PESD1 order-status tickets", _order_state, code_repos=("apollo",)),
]


FEATURE_REQUEST = re.compile(r"\bfeature request\b", re.I)


def is_feature_request(text: str) -> bool:
    """New work, not a fault — there is no root cause to find (PESD1-11393 matched 'money' on its loyalty-points tier)."""
    return bool(FEATURE_REQUEST.search(text or ""))


def choose(text: str, ents: Entities) -> list[Playbook]:
    """Playbooks that match the text and have the ids they need, best first; money first if it matches."""
    if is_feature_request(text):
        return []
    scored = []
    for p in PLAYBOOKS:
        s = p.matches(text)
        if s:
            scored.append((p.never_touch, s, p))
    scored.sort(key=lambda t: (not t[0], -t[1]))
    return [p for _, _, p in scored]
