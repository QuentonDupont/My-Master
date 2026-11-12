"""Investigator — find the root cause of a PESD1 ticket and write a step-by-step fix.

Library for flows/investigate.py; it orchestrates nothing. Read-only end to end:

    entities   — order, return, PO, product and SKU ids pulled from the ticket text
    playbooks  — known issue types: which checks to run, how to read them, how to fix
    catalog    — the saved query library (the Queries repo), rendered with those ids
    db         — runs catalogued SELECTs on spanky / henry through the read-only login
    code       — greps the local Pomelo repo clones for the code paths involved
    report     — the investigation packet a human reads and acts on

Added 12 Nov 2026 by the board owner (CLAUDE.md invariant 2): "add an investigative
layer of intelligence where you search production database, apollo platform, henry
platform and all pomelo repos on how to locate the root cause of issues, and provide a
step by step fix". A fix that changes data is written as SQL for a person to run —
nothing here writes to any database, Jira or repo.
"""
