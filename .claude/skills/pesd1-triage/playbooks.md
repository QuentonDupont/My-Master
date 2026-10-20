# PESD1 fix playbooks

One entry per ticket type: how it is actually fixed, who does it, how to
verify it, and the evidence. Written from Jira precedent, PRDT developer
comments, Confluence and Slack. **Evidenced** means someone wrote it down;
**Inference** means we worked it out and nobody has confirmed it yet.
Update an entry when a ticket teaches us something new. See step 8 of the
flow in SKILL.md.

Record paths use the NetSuite account `4449099.app.netsuite.com`.

---

## [Crashlytics] fatal crash alerts

*Looks like:* summary starting `[Crashlytics] [New Fatal Issue]`, reporter
Suresh Dhakal (the Firebase integration), low priority, no description beyond
the crash signature and app version. These arrive in volume and each is its
own crash, however alike the summaries read — never link two as duplicates.

**This is a fixed route, not a judgment call** (`config/boards.yml`
`fixed_routes`, applied by `fixed_route_gate` before research or duplicate
detection runs — reconfirmed by the board owner on PESD1-11335, 12 Oct 2026:
*"this ticket will always need to be processed by creating a PRDT ticket and
assigning it to Rahul"*):
1. PRDT clone, summary and description copied **verbatim** from the PESD1
   ticket — no rewriting, no fix brief.
2. Same priority as the source ticket.
3. Labels `business_continuity` + `tech_ops`; epic PRDT-11608 "[Crashlytics]
   Fatal crash alerts — Business Continuity" (via
   `development.epic_by_component.business_continuity`).
4. Assignee **Rahul Ravindra**, no alternates offered — this is the whole
   route, not a ranked guess.
5. Link Cloners; move the PESD1 parent to **To Do** (the standard transition
   rule, since this is a real dev handoff).

A PESD1 ticket that merely *mentions* Crashlytics in passing (e.g. "fails on
a Crashlytics-enabled build") does not match — the gate checks the summary
prefix, not the word anywhere in the ticket.

**Housekeeping:** PRDT-11609 through PRDT-11785 is the series so far, all to
Rahul under PRDT-11608. Still PROPOSED/To Do as of 12 Oct 2026 and routine to
approve in batches.

---

## NetSuite: create a new NCG item

*Looks like:* "Request to create new NCG item: `<name>` (`S_NCGxxxx`)" —
the current intake form's wording (PESD1-11326/27/28/33/34, 28 Sep–9 Oct
2026). Older tickets used "Request to Create New SKU – S_NCGxxxx"; don't
widen the match to that, or to "NCG" generally — PESD1-10877 "Unable to
Process Receipts for NCG Transfer Orders" is a different problem that
happens to share the word.

**This is a known route, not a NEEDS_CODE guess** (`config/boards.yml`
`assignee_overrides`, applied inside `plan_clone`): the developer is always
**Vishal Gaikwad**, under epic PRDT-10732 "NetSuite Support Tickets",
labels `netsuite` + `ncg`. Unlike the Crashlytics fixed route, this does
**not** skip research or the drafted comment — every NCG request has its
own missing details to ask about before anything is created:

1. The cost to set (purchase cost) — never given in the ticket itself.
2. Whether a quantity like "50 pcs" is a pack size or the unit of measure
   (precedent S_NCG0136 used Pieces as the unit).
3. The subsidiary (e.g. S_NCG0136 specified "Singapore (USD), Include
   Children" — not assumed).
4. Any dimension or size that repeats verbatim across two tickets in the
   same batch (PESD1-11333/11334 both said "30CM x 37CM x 15CM" for a
   "Big Size" and a standard bag — ask, don't assume a copy-paste error).

**Verify:** search the new item code in NetSuite and record the internal
ID (Vishal's comments always state it, e.g. "Netsuite InternalId
-2265324") — then confirm with the requester it appears in their NCG list.
PESD1-10667/S_NCG0140 shows why: Quenton said "completed," but the
requester couldn't find the item until a screenshot proved it.

**Housekeeping:** PESD1-11326/PRDT-11780 (S_NCG0144) was confirmed created
by Vishal but is still sitting To Do as of 12 Oct 2026 — close the loop.

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

## Apollo ↔ NetSuite: return (RMA) not syncing / wrong status

*Looks like:* "The return order is not syncing", "RMAxxxx still incorrect
status", "not updated in Superset". From TH Ops.

**Fix**
1. Apollo admin, open the return:
   `apollo.pomelofashion.com/sp/index.php?controller=AdminReturn&updateorder_return&id_order_return=<id>`
   and press **Sync RMA** (Ops tech linked this page on PESD1-11092; the
   warehouse used the button in #logistic_x_wh, 22 Sep 2026). This is a
   human with Apollo admin, never the system: invariant 2.
2. If it fails, read the error in NetSuite's **Integration log**
   ("NetSuite handbook", NIM space, /wiki/spaces/NIM/pages/2395930626).
3. `[INCORRECT_STATUS]` or no invoice → the original sales order is not
   Billed/Closed:
   - Fulfilment stuck at *Packed*: call
     `https://nimbus.pomelofashion.com/fulfillment/{orderid}/sync`, then
     retry step 1 ("How-to: RMA won't sync to from Apollo to NS", PM,
     /wiki/spaces/PM/pages/1303117969).
   - Or bill the sales order in NetSuite (Ajju Gupta, PRDT-10762, 27 Jan 2026).
4. A long customer memo also breaks the sync. Shorten it and re-trigger
   (PRDT-11453; a character limit is now Live).
5. Status going back to Apollo comes from the NetSuite scheduled script *INT
   API Return Order Status Update* (`customscript_int_api_sd_ro_status_update`).

**Who:** Wallop (Apollo, PESD1-11168), Vishal (NetSuite, PRDT-11030),
Ajju Gupta (billing, PRDT-10762). Steps 2–3 need NetSuite admin, so a developer.

**Verify:** Apollo and NetSuite show the same return status. The RMA
appears in Superset with that status (chart slice_id=1988, as on
PESD1-11139). Superset lag is a separate recurring symptom
(PESD1-11137, 11139, 11141).

**Recurs:** yes. PRDT-11570 (To Do, Vishal) lists 7 manual resyncs since 31
Jul with no root cause. PRDT-10802 (auto-create missing invoice) is Live
but hasn't stopped it. PRDT-11570 went **Live on 2 Oct 2026 with no root
cause recorded**, so new cases need a new or reopened tracker, not a link to it
(found on PESD1-11338, 20 Oct 2026).

**Refund risk, unresolved:** "Apollo Return Process" (PM,
/wiki/spaces/PM/pages/2865364993) says Return Received is set from
NetSuite, then a refund is "submitted" and a credit slip generated. The
2022 ENG design made this automatic. PRDT-10994: for MY, syncing
"automatically triggers receipt". Until a developer confirms, treat **setting
a return to Received by hand** (e.g. PESD1-11279) as never-touch (refunds).
A plain re-sync of a return that is already Received in NetSuite is fine.

### The actual queries, so "check NetSuite and Apollo" is not a vague gesture

**Apollo** is a MySQL-backed app, so a real `SELECT`/`DELETE` can be drafted
and handed to whoever has DB access (Quenton, or an engineer over SSH —
invariant 2 means the *system* never runs these itself, only drafts them for
a human, exactly as Quenton posted the DELETE SQL himself on PESD1-11305).

```sql
-- LOOKUP: one or more returns by id (the RMA number, no "RMA" prefix)
SELECT * FROM pml_order_return WHERE id_order_return IN (<id>, <id>, ...);
SELECT * FROM pml_order_return_detail WHERE id_order_return IN (<id>, ...);
SELECT * FROM pml_order_return_history WHERE id_order_return IN (<id>, ...);
```

```sql
-- DELETE: a return that should not exist (confirmed first, never guessed —
-- see the never-touch note below). Order matters: history and detail are
-- children of the return row (PESD1-11281, 11305 both ran it in this order).
DELETE FROM pml_order_return_history WHERE id_order_return = <id>;
DELETE FROM pml_order_return_detail WHERE id_order_return = <id>;
DELETE FROM pml_order_return WHERE id_order_return = <id>;
```

There is no verified **ADD**/insert query for this table — no precedent has
ever needed one, and guessing the required columns on a commerce table that
feeds NetSuite and refunds is how you create a return nobody can act on.
Treat a "recreate the return record" ask as its own, slower investigation,
not a one-line INSERT.

**Column names beyond `id_order_return` are not verified against the live
schema** — the `SELECT *` form above is deliberate: it shows every column
there is, rather than guessing at a `sync_status` or `received_at` name that
might not exist. Confirm columns against one real row before writing a
narrower query.

**NetSuite has no query access at all**, verified or otherwise — see
"Platform connections" below. "Check NetSuite" means the **Lookup** steps:
Global Search the RMA number, open the Return Authorization, and read
whether a Credit Memo or Item Receipt is already linked. There is no SQL,
SuiteQL or saved-search substitute available to this system today.

**Never-touch applies to the confirmation step too.** A DELETE is only ever
drafted after the ticket (or Quenton) has independently confirmed the record
is wrong — "the requester said so" is not confirmation on a table that
touches refunds. If that confirmation hasn't happened, the fix brief says
"not confirmed as erroneous" and stops there.

## Return wrongly auto-closed after a marketplace cancellation (reopen)

*Looks like:* "please reopen RMA/change status from Closed to Pending
Receipt", after a marketplace order (Shopee/Lazada/TikTok) was cancelled
**after** it had already shipped. The platform's own cancel flow then
auto-creates, auto-receives and auto-closes an RMA nobody asked for — the
parcel is still in transit and the warehouse has nothing to receive against
on arrival. First seen as a batch of 7 on PESD1-11319, 5 Oct 2026.

**This is refund-adjacent, not a plain data-entry fix — escalate, don't
execute.** Reopening means deleting the NetSuite Item Receipt, and by the
time Ops notices, a Credit Memo or refund may already be linked to it.
Touching either without Finance's say is invariant-3 territory even though
no never-touch keyword fires on the ticket text itself.

**Lookup, per RMA** (NetSuite, manual — no query access): open the Return
Authorization, and read whether an Item Receipt, Credit Memo or Refund is
linked. **Fix, only after Finance approves:** delete the Item Receipt only —
never the Credit Memo or Refund without a separate Finance decision — so the
RMA reads Pending Receipt again and the warehouse can receive the real
parcel. No precedent exists for reopening an already-received-and-closed RMA
specifically (distinct from PESD1-10081/PRDT-10406, which deleted a receipt
on a Transfer Order, not an RMA).

**Recurs, and is worth its own fix:** the root cause — MP-cancel-after-ship
auto-creating a return — is a real bug independent of any one ticket. Worth
raising as its own NEEDS_CODE ticket against the integration rather than
reopening RMAs by hand every time it happens.

## Henry: PO / invoice shows the wrong currency (THB / RMB / USD)

*Looks like:* "PO 33917 change the THB to RMB, INV#55619", or a list of
PO–invoice pairs. Always Selina (Yanqiong) Liu, Commercial, so far.

**Fix** (Henry DB, a human, invariant 2): back up the order and invoice rows,
then relabel the currency on the order, the invoice **and its lines** (since
PRDT-11366 the lines inherit it). **Don't convert the amounts.** They are
already in the right currency with the wrong label: PESD1-11358 showed
75.70 RMB lines under a "454.20 THB" total. If the invoice has synced to
NetSuite as a vendor bill, Vishal corrects the bill (PRDT-11368).

**Who:** Quenton (did PESD1-11008, 30 Jun 2026, 21 pairs; PESD1-11297,
12 Oct 2026). Unni owns the code (PRDT-11366/11367).

**Verify:** the PO currency field and the invoice totals (Total Original
Cost, Total After Discount, Amount Payable) read the target currency. A
synced NetSuite bill shows CNY/USD to match.

**Recurs:** yes, and the direction changes each time. The cleanup is
PRDT-11367 (Henry legacy data) / PRDT-11368 (NetSuite), both To Do, past due
18 Sep. Check the vendor's default currency too (inference).

## NetSuite: wrong SKU on a PO-TH-241xxxxx sample (PPS) PO

*Looks like:* "Mistake sku in Netsuite system — delete wrong SKU in
PO-TH-24100203". These are NetSuite-native sample POs keyed by the design
buyers, not Henry POs (Henry numbers look like 29748-51086).

**Fix:** open the PO and check the Receiving and Billing tabs. Delete, or
tick Closed on, **only lines with Received = 0 and Billed = 0**. Delete the
whole PO only when nothing on it is received (PESD1-10135, PESD1-8717).
A received or billed line touches an Item Receipt and stock, so it needs
Finance approval first (PESD1-2306).

**Who:** Quenton in NetSuite. Alternate: Vishal.

**Verify:** the PO reads Fully Received / Pending Billing, and the RF
receive screen shows no open lines. Search #trading-warehouse for the PO
number: the warehouse usually confirms there what physically arrived
(PESD1-11353, 19 Oct 2026).

**Recurs:** yes, from buyer keying errors. The lasting fix is buyer training.

## NetSuite: bins created under the wrong location

*Looks like:* "update it from TH Inbound Defect to TH Defect Grade C", with
a bin search link (location 190, "RT-") and a sheet of bins (PESD1-11350).
Requesters may call it a "work order".

**Fix:** run the bin search and confirm on-hand is 0 for every bin. Then
CSV-import an update of the Bin records' Location (TH Defect Grade C = 474).
**A bin holding stock needs an inventory transfer, which is never-touch
(stock).** Escalate it with no comment.

**Who:** Quenton. Alternate: Vishal. Nearest precedent: PESD1-11011 (TO
destination location change, Quenton, 1 Jul 2026). No exact bin precedent
exists.

## New store: NetSuite + Apollo location setup

*Looks like:* "Request to Create New Location – <store>" from Nonthakorn
Malai (Stores). SOP: Confluence PM 2872836098 "New Store and location
setup". Store Fulfilment: ENG 2320072707.

**Order:**
1. Check the main NetSuite location doesn't already exist (on PESD1-11235 it did).
2. Create the 4 NetSuite locations (store, Lost, NCG, Lost SF).
3. Create the NetSuite Site-to-Store department (Vishal made KH's, dept 122).
4. Put the NetSuite location ID on the Apollo location. The requester often creates the Apollo location herself first: 139 on PESD1-11352.
5. Create the Apollo "- TO" location and link the order type.
6. Add the store to `pml_pickup_location_shop` for Store Fulfilment.
7. Send the NetSuite ID to the Henry dev and to Rajasekaran for the Superset store cluster.

The SF SOP says "active"; the requester usually wants it inactive. Ask.

**Who:** Quenton (PESD1-11236, 10418, 10895, 10867, 10321, 10343).
Date-bound: it has to be done before the opening.

## Superset dataset or new field request

PRDT clone titled "[Data Engineering] - …", assigned to **Rajasekaran
Murugesan** (PRDT-10937, 10866, 10859, 10861, 10877, 10881, 11097, 11107,
11144). This corrects the 12 Oct 2026 HANDOFF note that no data-engineering
route exists. A dataset with staff names or emails is employee PII: the board
owner approves it before it is cloned, with role-restricted access
(PESD1-11332).

## PomQ POS feature request

Clone to **Bikesh Shakya Banda** (current [POMQ] dev: PRDT-11790, 11553,
11531, 11525) under epic **PRDT-11330**. Wallop owns the epic. Default to
priority Medium: the intake form files these as Critical Incidents
(PESD1-11355).

## Loyalty tier change (e.g. "Please Upgrade Tier" to Diamond)

**Never-touch, escalate.** Diamond is a paid subscription carrying pricing
benefits (PRDT-11000, PRDT-11509), on a customer account in Apollo. Quenton
has done these by hand (PESD1-11289, 10977, 11093); Wallop did PESD1-10355.
**Recurs** until PRDT-11531/11532 ("[POMQ] Offline Diamond tier
subscription purchase", Bikesh/Wallop, To Do) ship: offline purchases don't
upgrade the tier.

---

## Platform connections

What we can check a ticket against, and from where. Read-only, always.

| Source | Checks | From a cloud session |
|---|---|---|
| Jira PESD1/PRDT | precedent, dev comments | yes: Atlassian connector |
| Confluence (OP, PSD, PM, NEON, MUL, TSD, NIM) | SOPs | yes: Atlassian connector |
| Slack | fixes agreed in threads | yes: Slack connector (read; drafts only after approval) |
| Google Drive | ticket attachments, intake sheet | yes: Drive connector |
| **Superset MCP** `superset-mcp-th.pmlo.co/mcp` (Redshift: returns, inventory, TOs, NS-vs-ERPLY) | live record state: the best check-up source | **partly, since 12 Oct 2026** — see note below |
| Superset UI `superset.pomelofashion.com` | same data, as dashboards (e.g. slice 1988 for returns) | no: SSO login plus proxy |
| NetSuite SuiteAnalytics Connect (ENG 2907668483, draft) | SuiteQL | no: personal login only, no service account — **this is the actual gap.** Every return/RMA ticket needs NetSuite confirmed and nothing in this repo, or available to a cloud session, can read it at all |
| Apollo / Henry admin, production DBs | record state | no, and **by design** (invariant 2; SSH per engineer) |
| GitHub `pomelofashion/*` (repos.yml) | which commit/PR fixed a ticket | partly: needs `add_repo` for the org |

**Superset, updated 12 Oct 2026:** a `mcp__superset__*` connector (dataset
list/get/query, prepared-chart tools) is reachable from an interactive
Claude Code session and was used to read dataset 153 (`raw_fact_return`) for
six PESD1 return tickets that day. Not confirmed whether this is the same
`superset-mcp-th.pmlo.co` host this row originally meant, or a separate
connector added since. Two real limits either way: it is **read-only over
curated datasets** (no ad-hoc SQL, no guarantee a given dataset like staff
clock-in data exists or is visible to this account), and it is **still not
wired into My-Master's own code** — `core/` has no Superset client, so the
scheduled flows still cannot self-verify a return's Apollo-side status; only
an attended session checking by hand can, as happened today. Say which is
true in a fix brief: "checked via Superset (session)" vs "not checked."

**Never copy the Superset token from Slack.** It has been pasted in plain text
in several DMs. It belongs in Vault and env secrets only (invariant 7).
