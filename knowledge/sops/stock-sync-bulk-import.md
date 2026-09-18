# SOP: Stock not syncing after a bulk import

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
