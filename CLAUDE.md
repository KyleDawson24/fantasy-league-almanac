# CLAUDE.md -- house rules for espn-league-manager

Standing conventions for every session in this repo:

- Commits: first-person messages, NO AI attribution -- no Co-Authored-By,
  no "Generated with" trailers.
- Never push without Kyle's explicit go-ahead.
- Goldens move only with reviewed cause. The byte-diff harness pins 2026
  Week 7 (--matchup-period 7). Snapshot fixtures before regenerating and
  show Kyle the diff summary before any re-anchor.
- dbt parse + unit suite green at every commit.
- Single-checkout world: everything runs from this directory on main
  (.venv here, .env at root). Do not create git worktrees -- two dbt
  projects over one warehouse produced last-writer-wins schema races.
- Do NOT pip install new dbt adapters into .venv (dbt-core is pinned;
  resolver drift breaks the weekly run). Experiments get their own venv.
- tests/test_records_report.py is Kyle's untracked WIP -- ignore it.
- Linear is written from the Cowork PM thread only. End sessions with a
  report-back block instead of touching the tracker.
- CHANGELOG [Unreleased] is a curated staging area. Release ceremony =
  RELEASING.md, and it includes publishing the GitHub Release
  (gh release create --notes-file "RELEASE NOTES vX.Y.Z.md").

## Kyle's question log (standing instruction, 2026-07-26)

When Kyle asks a conceptual question in chat, append ONE dated line to
"Study Material.MD" under "Kyle's question log" -- extremely brief, e.g.
"7/26 asked about impact of git not tracking a file". Do not answer in
the entry; name where context lives if obvious. Recurring themes get a
line in that section's "Recurring confusions" subsection.

Also standing for Study Material.MD: append a curveball-index line
whenever something non-obvious gets solved, and "why this approach"
notes worth retelling in an interview.
