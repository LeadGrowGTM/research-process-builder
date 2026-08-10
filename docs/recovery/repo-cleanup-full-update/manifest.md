# Recovery inventory manifest

- Recorded status-era observation: 3558
- Authoritative preserved object-tree coverage: 3561
- Difference: +3
- Unexplained paths: 0

The earlier status-era observation was 3,558. The preserved object-tree coverage of 3,561 is authoritative for this inventory. This manifest does not infer a specific cause for the difference without further evidence.

## 2026-08-10 Phase 3 policy decision

- Recovery evidence checked: immutable recovery commit
  `e3932d55217c29ac28eca16fdc7e6f6c5c3e3337`, `inventory.csv`, and
  `quarantine-map.csv` (including object IDs and SHA-256 payload hashes).
- Restorations: none. No document-only or quarantined item was selectively
  restored because this policy phase found no reviewed reusable item requiring a
  destination in the tracked tree.
- Removals: none. No current-tree candidate had an explicit removal disposition;
  campaign/generated bulk remains outside the tracked tree in the ignored local
  quarantine.
## 2026-08-10 Phase 3 fix round 1 action record

`action-decisions.csv` is the machine-readable Phase 3 action record. It has
only its schema header and zero action rows, which is the authoritative proof
that this phase made no restoration or removal. Any future row must match the
inventory path, object ID, recovery command, and, when quarantine-backed, the
quarantine-map local path and SHA-256.
## 2026-08-10 Phase 3 fix round 2 action schema

The zero-row `action-decisions.csv` record now includes `recovery_commit` and is
validated by `scripts/recovery_inventory.py`. Empty remains valid; any future
action must be disposition-authorized and match inventory plus quarantine-map
evidence before it can be recorded.