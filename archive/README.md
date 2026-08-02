# Archive

Frozen artifacts kept for posterity — historical reference only, not consumed
by any active code path.

**Which "archive" is this?** This one holds *code exhaust* — retired scripts
and scaffolding. `docs/archive/` holds *prose*: the phase documentation,
session handoffs and progress journals. `archives/` is local-only safekeeping
and is not in git at all.

Paths written inside the documents below describe the repository as it was
when they were written. Several name files "at repo root" that MLB-154 moved
to `docs/archive/` on 2026-08-02; the documents are left as written rather
than back-edited, because they are a historical record.

Naming convention for individual artifacts: `phase_<X.Y>_<topic>__<descriptor>.<ext>`.
The phase prefix ties each file back to the work that produced it;
cross-reference the matching `Phase X.Y Documentation.md` in `docs/archive/`
for context. Working-doc bundles (e.g., `phase_7_working/`) live in their
own subdirectories and preserve the doc-style filenames they had at repo
root.

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

- **`phase_7_working/`** — internal scaffolding from the Phase 7 rearchitect:
  cross-session continuation briefs, the architecture review that kicked
  off the phase, and the Phase 7 kickoff handoff. These were coordination
  artifacts between Claude Code sessions, not canonical project record —
  the canonical Phase 7 story lives in `Phase 7 Documentation.md` in
  `docs/archive/`. Useful for "how did we get here" archaeology if anyone
  needs to retrace a decision.
