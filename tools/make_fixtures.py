"""Generate the offline fixture set in tests/fixtures/.

Deterministic, no network. `tools/seed_demo.sh` loads these into the corpus so
the whole pipeline can be exercised without a Jira token.
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
EMAIL_FIELD = "customfield_10201"


def issue(key, summary, description, status, *, resolution=None, assignee=None,
          reporter="Support Desk", labels=(), components=(), created="2026-06-01T09:00:00.000+0700",
          updated="2026-06-05T09:00:00.000+0700", comments=(), requester_email=None):
    fields = {
        "summary": summary,
        "description": description,
        "status": {"name": status},
        "resolution": {"name": resolution} if resolution else None,
        "created": created,
        "updated": updated,
        "reporter": {"displayName": reporter},
        "assignee": {"displayName": assignee} if assignee else None,
        "labels": list(labels),
        "components": [{"name": c} for c in components],
        "priority": {"name": "Medium"},
        "issuetype": {"name": "Support"},
        "comment": {"comments": [{"id": str(9000 + i), "body": b,
                                  "author": {"displayName": "Support Desk"}}
                                 for i, b in enumerate(comments)]},
    }
    if requester_email:
        fields[EMAIL_FIELD] = requester_email
    return {"key": key, "fields": fields}


HISTORY = [
    issue("PESD1-10233", "Stock not syncing from warehouse after bulk import",
          "After the nightly bulk import the stock levels in Apollo stay at the old "
          "value. Warehouse shows 40 units, storefront shows 0. Happens on the THA site.",
          "Done", resolution="Done", assignee="Somchai Prasert", labels=["stock-sync"],
          components=["stock-sync"],
          comments=["Root cause: the import job dropped the delta file when the SFTP "
                    "connection timed out. Fixed by retrying the fetch. See PRDT-402."]),
    issue("PESD1-10450", "Out of stock products still purchasable on PDP",
          "Products with zero inventory are still showing as available on the product "
          "page and can be added to cart. Stock sync seems delayed.",
          "Done", resolution="Done", assignee="Somchai Prasert", labels=["stock-sync"],
          components=["stock-sync"],
          comments=["Cache TTL on the availability service was 6h. Reduced to 5m in PRDT-410."]),
    issue("PESD1-10688", "Voucher STYLE15 not applying at checkout",
          "Customers report the STYLE15 voucher is rejected at checkout with 'invalid "
          "code' even though the campaign is live.",
          "Done", resolution="Done", assignee="Nadia Rahman", labels=["checkout"],
          components=["checkout"],
          comments=["Cart rule had a currency condition that excluded THB. Fixed in PRDT-455."]),
    issue("PESD1-10901", "Product images missing on category page",
          "Category listing shows placeholder images for about 200 SKUs uploaded last week.",
          "Done", resolution="Done", assignee="Pim Wattana", labels=["catalog"],
          components=["catalog"],
          comments=["Image CDN ingestion failed for filenames with spaces. PRDT-470."]),
    issue("PESD1-11002", "Order tracking link returns 404",
          "The tracking link in the shipping confirmation email returns a 404 page for "
          "orders shipped from the Bangkok warehouse.",
          "Done", resolution="Done", assignee="Tom Nguyen", labels=["order"],
          components=["order"],
          comments=["Tracking URL template used the old carrier path. PRDT-488."]),
    issue("PESD1-11150", "Stock sync job stuck at 03:00 every night",
          "The stock sync job hangs at 03:00 and has to be restarted manually. "
          "Inventory is stale until someone notices.",
          "Done", resolution="Done", assignee="Somchai Prasert", labels=["stock-sync"],
          components=["stock-sync"],
          comments=["Deadlock on the inventory table during the nightly reindex. PRDT-502."]),
    issue("PESD1-11190", "How do I export the daily order report?",
          "I need the daily order report for the merchandising team. Where do I get it?",
          "Done", resolution="Done", assignee="Pim Wattana", labels=["report"],
          comments=["Apollo > Reports > Orders > Daily, then Export CSV. Scheduled exports "
                    "can be set up under Reports > Schedules. Documented in the SOP."]),
    # still open — the duplicate target
    issue("PESD1-11270", "Stock levels not updating after bulk import",
          "Bulk import ran last night on the THA warehouse but the storefront still shows "
          "the old stock numbers. Warehouse team confirms the file was uploaded.",
          "Waiting for Support", reporter="Warehouse Ops", labels=["stock-sync"],
          components=["stock-sync"], created="2026-09-15T08:10:00.000+0700"),
]

DEV = []
_dev_specs = [
    ("PRDT-402", "Retry SFTP fetch in stock delta import", "Somchai Prasert", "stock-sync"),
    ("PRDT-410", "Reduce availability cache TTL", "Somchai Prasert", "stock-sync"),
    ("PRDT-432", "Backfill stock for THA warehouse", "Somchai Prasert", "stock-sync"),
    ("PRDT-455", "Fix currency condition on cart rule", "Nadia Rahman", "checkout"),
    ("PRDT-470", "Handle spaces in image filenames", "Pim Wattana", "catalog"),
    ("PRDT-488", "Update tracking URL template", "Tom Nguyen", "order"),
    ("PRDT-502", "Fix deadlock in nightly inventory reindex", "Somchai Prasert", "stock-sync"),
    ("PRDT-511", "Stock sync alerting", "Somchai Prasert", "stock-sync"),
    ("PRDT-520", "Checkout voucher validation logging", "Nadia Rahman", "checkout"),
    ("PRDT-533", "Catalog attribute import fix", "Pim Wattana", "catalog"),
    ("PRDT-540", "Stock sync idempotency key", "Somchai Prasert", "stock-sync"),
    ("PRDT-551", "Order export column order", "Tom Nguyen", "order"),
]
for i, (key, summary, who, comp) in enumerate(_dev_specs):
    DEV.append(issue(key, summary, f"Development work for {comp}. {summary}.",
                     "Done", resolution="Done", assignee=who, labels=[comp],
                     components=[comp],
                     updated=f"2026-0{(i % 8) + 1}-1{i % 9}T10:00:00.000+0700"))

INBOX = [
    issue("PESD1-11274", "Stock levels not updating after bulk import on THA warehouse",
          "We ran the bulk import for the THA warehouse yesterday evening. The storefront "
          "still shows the previous stock numbers this morning, so oversells are starting. "
          "Warehouse confirms the delta file was uploaded at 21:40.",
          "Waiting for Support", reporter="Warehouse Ops", labels=["stock-sync"],
          created="2026-09-16T07:55:00.000+0700"),
    issue("PESD1-11275", "Voucher LOOK20 rejected at checkout for Thai customers",
          "Customers using the LOOK20 code get 'invalid code' at checkout. The campaign is "
          "live and the code works on the SG site. Needs a fix before the weekend push.",
          "Waiting for Support", reporter="Marketing TH", labels=["checkout"],
          requester_email="marketing.th@pomelofashion.com",
          created="2026-09-16T09:20:00.000+0700"),
    issue("PESD1-11276", "Where do I find the daily order report?",
          "I need the daily order export for the merchandising review. Can you point me to "
          "where it lives in Apollo?",
          "Waiting for Support", reporter="Merch Team",
          created="2026-09-16T10:05:00.000+0700"),
    issue("PESD1-11277", "Refund not received by customer for order TH-993120",
          "Customer says the refund for order TH-993120 has not arrived after 10 days. "
          "Please process the refund again and confirm the payment reference.",
          "Waiting for Support", reporter="CS Team",
          created="2026-09-16T11:00:00.000+0700"),
    issue("PESD1-11278", "it's broken again",
          "same as before, pls fix asap",
          "Waiting for Support", reporter="Store Ops",
          created="2026-09-16T12:00:00.000+0700"),
    issue("PESD1-11279", "Product images missing for SS26 upload batch",
          "About 120 SKUs from the SS26 batch uploaded on Monday show placeholder images "
          "on the category page. The files were named with spaces in them.",
          "Waiting for Support", reporter="Studio Team", labels=["catalog"],
          created="2026-09-16T13:30:00.000+0700"),
]

SHEET = """timestamp,requester_name,requester_email,jira_ticket,request
2026-09-16 07:50,Warehouse Ops TH,warehouse.th@pomelofashion.com,PESD1-11274,Bulk import stock not updating on the storefront
2026-09-16 09:15,Marketing TH,marketing.th@pomelofashion.com,,LOOK20 voucher is rejected at checkout for Thai customers
2026-09-16 10:00,Nok Merchandising,nok.m@pomelofashion.com,,Where can I download the daily order report
2026-09-16 13:25,Studio Team,studio@pomelofashion.com,,SS26 images not showing on category page
"""

SOP = """# SOP: Stock not syncing after a bulk import

## Symptom
Storefront stock is stale after a bulk import; the warehouse shows different numbers.

## Scope
- Component(s): stock-sync
- Signals: bulk import, delta file, sftp, availability cache, oversell

## Resolution
1. Check the import job log for a dropped or timed-out SFTP fetch (PESD1-10233).
2. Check the availability cache TTL; it should be 5 minutes, not 6 hours (PESD1-10450).
3. If the job is hung, look for a deadlock on the inventory table (PESD1-11150).
4. Re-run the delta import for the affected warehouse, then verify one SKU end to end.

## Next time
Escalate to the stock-sync component owner if all three checks pass and stock is still stale.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, issues in (("pesd1.jsonl", HISTORY), ("prdt.jsonl", DEV),
                         ("inbox.jsonl", INBOX)):
        (OUT / name).write_text(
            "\n".join(json.dumps(i, ensure_ascii=False) for i in issues) + "\n",
            encoding="utf-8")
    (OUT / "intake_sheet.csv").write_text(SHEET, encoding="utf-8")
    sop_dir = OUT.parent.parent / "knowledge" / "sops"
    sop_dir.mkdir(parents=True, exist_ok=True)
    (sop_dir / "stock-sync-bulk-import.md").write_text(SOP, encoding="utf-8")
    print(f"fixtures written to {OUT}")


if __name__ == "__main__":
    main()
