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

## Apollo ↔ NetSuite: return (RMA) not syncing / wrong status

*Looks like:* "The return order is not syncing", "RMAxxxx still incorrect
status", "not updated in Superset". From TH Ops.

**Step 0: check the sync on BOTH platforms before any fix** (the board
owner's process, taught on PESD1-11282, 28 Sep 2026). Many of these are
already synced by the time we look. For every RMA in the ticket (the numbers
are in *Steps to reproduce*; don't ask the requester for them):
- **NetSuite:** global search `RMA<number>`. Synced = a **Return
  Authorization RMA<number>** exists (created by *Pomelo Integration*). A
  **Credit Memo CN-TH-…** for the same customer means the refund side ran too.
  Read-only URL, works from the owner's logged-in Chrome:
  `system.netsuite.com/app/common/search/ubersearchresults.nl?quicksearch=T&searchtype=Uber&frame=be&Uber_NAMEtype=KEYWORDSTARTSWITH&Uber_NAME=RMA<number>`
- **Apollo:** Orders › Merchandise Returns › Edit, where `id_order_return` =
  the RMA number without "RMA". Synced = the **NetSuite Info** box shows
  **Sync Status: Yes**, with the RMA number and an updated date. Note the
  Return Status too (e.g. *Return Received*). A deep link lands on
  PrestaShop's "Invalid security token" page, and the auto-mode classifier
  blocks clicking through it. Reach the page through the admin menu, or
  have the owner check it.

Then:
- **Synced on both** → comment "Completed" with the evidence for each RMA
  (Apollo sync status + date, NetSuite RA + credit memo numbers; screenshots
  if you have them), then move the ticket to **Live**. No fix needed.
- **Synced on only one platform** → **stop. Don't attempt a fix.** Tell the
  board owner which RMA is missing where; he resolves these himself.
- **On neither** → the Fix below.

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
but hasn't stopped it. Link new cases to PRDT-11570.

**Refund risk, unresolved:** "Apollo Return Process" (PM,
/wiki/spaces/PM/pages/2865364993) says Return Received is set from
NetSuite, then a refund is "submitted" and a credit slip generated. The
2022 ENG design made this automatic. PRDT-10994: for MY, syncing
"automatically triggers receipt". Until a developer confirms, treat **setting
a return to Received by hand** (e.g. PESD1-11279) as never-touch (refunds).
A plain re-sync of a return that is already Received in NetSuite is fine.

---

## Platform connections

What we can check a ticket against, and from where. Read-only, always.

| Source | Checks | From a cloud session |
|---|---|---|
| Jira PESD1/PRDT | precedent, dev comments | yes: Atlassian connector |
| Confluence (OP, PSD, PM, NEON, MUL, TSD, NIM) | SOPs | yes: Atlassian connector |
| Slack | fixes agreed in threads | yes: Slack connector (read; drafts only after approval) |
| Google Drive | ticket attachments, intake sheet | yes: Drive connector |
| **Superset MCP** `superset-mcp-th.pmlo.co/mcp` (Redshift: returns, inventory, TOs, NS-vs-ERPLY) | live record state: the best check-up source | **no.** The host is not in the network allowlist and there's no token in the env secrets. Works in Claude Desktop (Raj, Aug 2026). The key is in Vault `pmlo/services/superset` |
| Superset UI `superset.pomelofashion.com` | same data, as dashboards (e.g. slice 1988 for returns) | no: SSO login plus proxy |
| NetSuite SuiteAnalytics Connect (ENG 2907668483, draft) | SuiteQL | no: personal login only, no service account |
| Apollo / Henry admin, production DBs | record state | no, and **by design** (invariant 2; SSH per engineer) |
| NetSuite UI via the owner's Chrome (local session only) | RMA / credit memo exists (global search) | local Mac session: **yes, read-only** (verified 28 Sep 2026 on RMA1430328). Never edit or save a record |
| Apollo admin via the owner's Chrome (local session only) | return's NetSuite Info sync status | local: blocked. A deep link hits "Invalid security token" and the classifier refuses the click-through (28 Sep 2026). Owner checks, or grants permission |
| GitHub `pomelofashion/*` (repos.yml) | which commit/PR fixed a ticket | partly: needs `add_repo` for the org |

**To turn on live check-ups:** the owner adds `superset-mcp-th.pmlo.co` to
the environment's network allowlist, and `SUPERSET_MCP_TOKEN` to its secrets
(or adds the server as a claude.ai custom connector). That is a new
read-only integration, so it needs his approval (CLAUDE.md invariant 2).
Until then, write "not verified against Superset" in a fix brief rather
than implying a check was made.

**Never copy the Superset token from Slack.** It has been pasted in plain text
in several DMs. It belongs in Vault and env secrets only (invariant 7).
