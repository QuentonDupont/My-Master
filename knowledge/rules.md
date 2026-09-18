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

_None approved yet._

## Phrasing

<!-- e.g. "Do not open a comment with an apology." -->

_None approved yet._
