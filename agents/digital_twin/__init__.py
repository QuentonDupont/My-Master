"""Digital Twin — a personal Chief of Staff for the board owner.

Read and draft only. It reads the sources the person has allowed, keeps a Work
Profile and a Task List, writes morning and end-of-day updates, and drafts
messages in their style. It never sends anything: there is no write client in
this package and no path to one. Sending is the person's step, done in the app
under their own name, after which the twin records the proof.

Like `agents/marketing_onsite`, it reports to the board owner directly, not
through the Jira Leader or the Chief of Staff.
"""
