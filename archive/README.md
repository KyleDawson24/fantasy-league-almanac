# Archive

Frozen artifacts kept for posterity — historical reference only, not consumed
by any active code path.

Naming convention: `phase_<X.Y>_<topic>__<descriptor>.<ext>`. The phase prefix
ties each file back to the work that produced it; cross-reference the
matching `Phase X.Y Documentation.md` in the repo root for context.

Files here are safe to delete if no longer interesting; they are NOT
maintained as the codebase evolves.

## Contents

- **`phase_3.3_doubleheader_debug__turang_raw.json`** — raw ESPN API response
  for Brice Turang during the Phase 3.3 investigation that uncovered the
  silent doubleheader stat-overwrite bug in `espn-api`'s `box_scores()`
  (the wrapper builds a dict keyed by `scoringPeriodId` and silently drops
  one game when ESPN returns multiple splits for the same period). Captured
  while diagnosing why Hosstros MP1 2026 totals were off by ~3.6 pts. See
  Phase 3.3 Documentation for the full investigation.
