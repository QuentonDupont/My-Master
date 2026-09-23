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

## Platform connections

What we can check a ticket against, and from where. Read-only, always.

| Source | Checks | From a cloud session |
|---|---|---|
| Jira PESD1/PRDT | precedent, dev comments | yes: Atlassian connector |
| Confluence (OP, PSD, PM, NEON, MUL, TSD, NIM) | SOPs | yes: Atlassian connector |
| Slack | fixes agreed in threads | yes: Slack connector (read; drafts only after approval) |
| Google Drive | ticket attachments, intake sheet | yes: Drive connector |
| NetSuite / Apollo / Henry / Superset | live record state | to be filled in: see below |
