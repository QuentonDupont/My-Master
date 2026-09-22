# Approved rules

Every rule in this file is injected into every worker's context, verbatim.

**Only the human adds a rule here.** The Chief of Staff proposes rules weekly by
clustering `knowledge/corrections.jsonl`; its output goes to `review/`, never here:

```
python -m agents.chief_of_staff.rules --write
```

Keep each rule to one line, imperative, and testable. Say what to do, not why.

## Routing

<!-- e.g. "Route stock-sync tickets to Somchai Prasert unless the label says apparel." -->

_None approved yet._

## Handling

<!-- e.g. "Never promise a delivery date in a PESD1 comment." -->

- Default to ANSWERABLE, not NEEDS_CODE or ESCALATE, unless a developer change
  is actually required — the human has reclassified worker output this way
  10 times (6 NEEDS_CODE→ANSWERABLE, 4 ESCALATE→ANSWERABLE). Approved 2026-09-21.

## Phrasing

<!-- e.g. "Do not open a comment with an apology." -->

- For a NetSuite COGS-not-posted report caused by zero on-hand stock at
  fulfilment, do not write "reviewed the report and it needs a change on our
  side." Write instead: "Closing the loop on this one with the investigation
  result from the NetSuite developer. The COGS GL was not recorded for these
  orders because the SKUs had zero inventory on hand at the time of
  fulfilment, specifically at TH Bangna Warehouse. With no on-hand quantity
  NetSuite cannot determine the item cost, so it does not post a COGS GL
  impact. Once stock is available again at that location the COGS GL will
  post correctly. This is working as designed rather than a defect, so no
  code change is planned. If you see this on an order where the SKU did have
  stock at Bangna at the time of fulfilment, reply here with the order number
  and we will look at that case specifically." Approved 2026-09-21.
