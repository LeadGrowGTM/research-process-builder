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