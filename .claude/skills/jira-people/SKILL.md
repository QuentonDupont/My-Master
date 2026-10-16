---
name: jira-people
description: Use when deciding who a PESD1 ticket or PRDT clone goes to, who a requester or resolver is, or who to name in a handover ("process this ticket for Daniel", "who fixes NetSuite / Henry / Apollo / Android / PIMS tickets"). The Jira board roster - names, areas, routing evidence and the standing assignment rules - so it does not have to be re-derived from corrections.jsonl each session.
---

# Jira people and routing

Who handles what on the PESD1 intake board and the PRDT development board.
Every row cites the tickets or the dated instruction it came from. A row with no
evidence is marked *(inferred)* and should be confirmed before it is relied on.

Display names are exactly as Jira shows them. Use them verbatim when proposing
an assignee. Jira account ids are not stored here: the search results this was
built from returned display names only, so look the id up at execute time.

## Roster

| Person | Area | Evidence |
|---|---|---|
| **Quenton Ryan Du Pont** | Board owner and the approver for every Jira write. Also fixes manual-ops tickets himself in Henry / NetSuite / Apollo and comments "Completed" with a screenshot. Default assignee when no developer is known. | CLAUDE.md invariants; PESD1-10921; `knowledge/corrections.jsonl` |
| **Vishal Gaikwad** | **NetSuite**: new NCG items, Item Receipt inventory detail and bins, locations, RMA to NetSuite sync fixes. Epic PRDT-10732 "NetSuite Support Tickets". Prefix `[netsuite]`. | PRDT-11607, 11570, 11564, 11559, 11030; `assignee_overrides: ncg_item_creation` in `config/boards.yml`; PESD1-11283 correction 28 Sep 2026 |
| **Unni Purushothaman** | **Henry** (prefix `[HEN]`): PO sizing, HS codes, franchise PO cost, size rows backfill. | PRDT-11451, PRDT-11341; PESD1-10921 |
| **Wallop (Pom) Thongdon-ngaw** | **Apollo** back-office and Apollo/NetSuite return sync on the Apollo side. | PESD1-11168; routing note "closed 18 of the last 34 PRDT tickets with component=apollo" (1 Oct 2026) |
| **Daniel Goncalves** | **PIMS / Backend**: store fulfilment enable/disable, stock availability and order-fulfilment backend. Prefix `[PIMS, Backend]`. Stated 16 Oct 2026 by the board owner: PESD1-11347 (orders pushed to J&T before warehouse dispatch, with missing item details and some items out of stock) goes to Daniel. | PRDT-11472, 11518, 11224; PESD1-11302 correction 29 Sep 2026 |
| **Rahul Ravindra** | **Android / Crashlytics**. Every `[Crashlytics]` PESD1 alert is cloned verbatim to PRDT and assigned to Rahul, no alternates. Epic PRDT-11608. | `fixed_routes: crashlytics` in `config/boards.yml`; reconfirmed on PESD1-11335, 12 Oct 2026 |
| **Suresh Dhakal** | Reporter of the Firebase Crashlytics integration alerts. Also the required reviewer on pomelofashion pull requests. | playbooks.md Crashlytics section; global memory `feedback_pr_reviewers` |
| **"Ops tech" account** | A shared TH Ops account. Appears as requester and as resolver of operational tickets. Not a developer. | playbooks.md NetSuite IR section |
| **Bikesh Shakya Banda** | Listed as an alternate assignee on a 1 Oct 2026 Apollo-related review and removed again. Area unknown. | `knowledge/corrections.jsonl` |

## Routing by what the ticket is about

1. **Matches a `fixed_routes` entry** in `config/boards.yml` (Crashlytics): clone
   verbatim, labels and assignee from the entry. Skip duplicate search and
   analysis.
2. **Matches an `assignee_overrides` entry** (NCG item creation): assignee
   fixed, but still research the case and write a case-aware comment.
3. **Otherwise route by the ticket's own fields first.** `Platform(s) Impacted`,
   `Expected Outcome` and the description beat the heuristic analyst. The
   PESD1-11283 correction exists because triage ignored `Platform=NetSuite`.
   - NetSuite, IR, bin, NCG, location, RMA-to-NetSuite -> Vishal
   - Henry, PO, sizing, HS code, MC -> Unni
   - Apollo admin / back office -> Wallop
   - PIMS, stock availability, store fulfilment, order fulfilment backend,
     carrier or label push triggered from fulfilment state -> Daniel
   - Android app crash -> Rahul
4. **Manual-ops tickets** (edit a field or record directly in Henry, NetSuite
   or Apollo) are not dev work. Precedent is Quenton fixing it directly. Do not
   clone these to PRDT.
5. **A person named by the board owner in chat wins** over every rule above.
   Record it here with the date and the ticket.

## How a clone is shaped

From `config/boards.yml` and the corrections history:

- PRDT issue type **Story**, summary prefix in the owner's style:
  `[netsuite]`, `[HEN]`, `[PIMS, Backend]`, `[Crashlytics]`.
- Link type **Cloners** to the PESD1 ticket. Priority is carried from the source.
- Epic by first label: `netsuite` -> PRDT-10732, `api` -> PRDT-10842,
  `business_continuity` -> PRDT-11608, everything else -> PRDT-11563
  "PESD1 Support Escalations".
- Move the PESD1 parent to **To Do** ("Submit to Dev") only when a dev clone
  exists. See `pesd1-triage` and the global memory
  `feedback_pesd1_triage_transition`.
- Every comment, clone, link, assignment and transition is a Jira write and
  needs the owner's approval first (CLAUDE.md invariant 1). Drafting is free.

## Adding to this file

When the board owner states or corrects who something goes to, add or amend the
row here in the same session, with the date and the ticket key, and commit it.
If a person's account id or an area becomes known, add it. Keep one fact per
row and say when something is inferred rather than observed.
