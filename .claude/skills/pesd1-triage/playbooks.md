# PESD1 fix playbooks

One entry per ticket type: how it is actually fixed, who does it, how to
verify it, and the evidence. Written from Jira precedent, PRDT developer
comments, Confluence and Slack. **Evidenced** means someone wrote it down;
**Inference** means we worked it out and nobody has confirmed it yet.
Update an entry when a ticket teaches us something new. See step 8 of the
flow in SKILL.md.

Record paths use the NetSuite account `4449099.app.netsuite.com`.

---

## NetSuite: Inventory Detail / bin missing on an Item Receipt (IR)

*Looks like:* "Please edit/correct the inventory details field IR:xxxx /
TO-xxxx", "destination box number not showing", "move to box PD-HOLD-xxx /
Cancellation_TH_Omni". Usually TH Ops, via the "Ops tech" account.

**Fix** (only the tech team can do it: it needs the NetSuite Administrator role)
1. Open the Item Receipt. Search the IR number, or open the TO and go to its
   related receipt. If the goods were first received on a different TO, fix
   **that** TO's receipt first (TSD SOP).
2. Make sure the target bin exists and is active. If it is inactive:
   Lists > Bins > edit > untick INACTIVE ("How-to: Re-Activate NS bin", PM).
   If it doesn't exist, stop and ask. PRDT-8814 was closed Won't Do for this reason.
3. Edit the IR. On each item line, open Inventory Detail, set Bin = the
   requested bin with the full received quantity, OK, Save. *(Inference:
   standard NetSuite clicks. Past fixes only show screenshots.)*
4. Only the Inventory Detail may be edited. The IR must not be deleted or
   otherwise changed, for audit reasons (PESD1-1344; TSD SOP).

**Who:** Quenton, directly on the PESD1 ticket with no PRDT clone (2025–26:
PESD1-9666, 9751, 10197, 10312, 10641, 10747, 10898, 10980). Earlier it went
through PRDT: Narunart (2023–24), then Saif (Sep 2024).

**Verify:** reopen the IR and check Inventory Detail shows the requested bin
and quantity. If the goods ship onward, check the next TO can be fulfilled
(PRDT-9114). Reply "Completed." with the IR link and a screenshot, as in
PESD1-10980.

**Root cause:** staff receive the TO manually without choosing a bin
(PRDT-8949 "same old root cause, they never follow process for receiving TO
correctly"; PRDT-9114). **Recurs:** yes, 17+ tickets from 2023 to 2026. The
lasting fix is training on "NetSuite to receive Transfer Order manually"
(NIM, https://pomelofashion.atlassian.net/wiki/spaces/NIM/pages/1173029117),
or a PRDT change to make the bin mandatory on manual TO receipt.

**Sources:** TSD "SOP Manually Receiving Transfer Orders"
https://pomelofashion.atlassian.net/wiki/spaces/TSD/pages/2398650369 ·
PESD1-10980, 10922, 10898, 10747, 10641, 1344 · PRDT-8949, 9114, 8814.

---

## Henry: sizes missing on a PO, shipment or invoice

*Looks like:* "no size detail shows on Henry", "no sizes and cannot create
the invoice", "XXL missing on shipment". Usually Commercial or Trading.

**Fix**
1. Open the Henry product page for the style
   (`henry.pomelofashion.com/product/<id>/view`) and check each size is
   **active**. Deactivated sizes are the usual cause (PESD1-11104: Quenton,
   3 Aug 2026, "the sizes were deactivate on Henry, please activate them";
   PESD1-10865 / PRDT-11154, "Activated", 20 May 2026).
2. If any are inactive, activate them. An admin Henry user can do this, and
   so can the requester. No developer needed. The SOP "Resolving Common
   Henry Platform Issues" §3B–3C covers editing sizes and triggering a
   manual sync before escalating:
   https://pomelofashion.atlassian.net/wiki/spaces/PM/pages/3001188353
3. If every size is active and the shipment still shows nothing, a
   developer (Unni) has to backfill the size rows in the DB, taking a backup
   first. Sizes live in `product_attribute` (`id_product`, `id_size_supplier`,
   `id_size_pomelo`, `sku_complete`, `ns_sync_status`, `active`). See
   PESD1-10921 (Unni, 29 May 2026). *(Inference: shipment-level backfill has
   no written precedent.)*

**Verify:** reload the order's shipment view and check the size breakdown
lists every size with quantities. Then check the invoice can be created.

**Root cause:** *inference.* Sizes deactivated, or added after the
shipment was created, so the shipment has no active size lines to show.
**Recurs:** yes, as a data issue (PESD1-11061, 11104, 10865).

---

## Henry: franchise PO cost doesn't match the TH (main) PO

*Looks like:* "unit costs do not match the TH order", "cost in franchise PO
didn't link with Main PO". From the Franchise Team, and it often blocks
production.

**Root cause** (evidenced in Slack and on tickets): franchise MCs/POs are
**cloned from the TH main MC** (#franchise_trading, Kevin Kiartisak and
Yaimai, Sep 2026). Cost is copied once, when the clone is made. Later cost
edits or order-spreadsheet uploads on the main MC do **not** reach the
clones (PESD1-11202, PESD1-11205, both open with no PRDT clone). For
PESD1-11285, the Dec26 Mens main MC was repriced on 22 Sep, after the
clones were made.

**Fix for existing POs:** a database bulk update copies main-MC unit cost
onto the franchise order lines. It's done by Unni, or by Quenton by query
(#franchise_tech, 9 Jul: "i use a database query to update them"; the
factory committed bulk ready date must be set or the update does not
stick). Precedent: PESD1-11209 / PRDT-11471 (Live, 11 Sep), which listed
the MC → franchise MC pairs. Buyers can also edit cost in the UI, one item
at a time.

**Verify:** for every SKU on the franchise POs, unit cost and currency equal
that SKU's cost on the TH PO, in both the factory and franchise views.

**Recurs:** yes, every time the main MC is repriced after cloning.
**Lasting fix:** cascade main-MC cost changes to its linked franchise
MCs/orders. That's the open ask in PESD1-11202 and PESD1-11205. Check
PRDT-11516 (decimal wholesale price, 15 Sep) for regressions on the same
path.

**Sources:** PESD1-11209, 11226, 11202, 11205, 11190 · PRDT-11471, 11516,
11052 · "Franchise PO Module"
https://pomelofashion.atlassian.net/wiki/spaces/henry/pages/2379939842
(nothing on cost sync).

---

## Platform connections

What we can check a ticket against, and from where. Read-only, always.

| Source | Checks | From a cloud session |
|---|---|---|
| Jira PESD1/PRDT | precedent, dev comments | yes: Atlassian connector |
| Confluence (OP, PSD, PM, NEON, MUL, TSD, NIM) | SOPs | yes: Atlassian connector |
| Slack | fixes agreed in threads | yes: Slack connector (read; drafts only after approval) |
| Google Drive | ticket attachments, intake sheet | yes: Drive connector |
| NetSuite / Apollo / Henry / Superset | live record state | to be filled in: see below |
