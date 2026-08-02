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
- Five owner seeds under dbt_league/seeds/ are `skip-worktree`: the file
  ON DISK is real league data, the file COMMITTED carries synthetic names
  and real member GUIDs -- no email or phone data is ever committed
  (MLB-95). The GUIDs are retained deliberately: identity resolution
  depends on them. Do not describe the committed seeds as "fully
  anonymized" -- they are name-anonymized, not identifier-anonymized.
  Reading the working copy and concluding "real names/phones are tracked
  in git" is a FALSE ALARM and a recurring one -- `git ls-files` lists
  the path because the path is tracked, not because that content is.
  Before reporting any leak from these files, confirm with `git show
  HEAD:<path>`, which is what actually shows the committed bytes. Still
  report a real finding; just check HEAD first. The mirror image is also
  a false alarm: a franchise or owner name that the README uses and the
  WAREHOUSE cannot find is almost always the twin doing its job, not an
  invented claim -- the warehouse holds real data, the README holds the
  committed twins. Verify anything about franchises, teams or owners BY
  ID, resolving name -> id through cbs_franchises.csv /
  owner_nicknames.csv first. Real MLB player names are not twinned and
  are safe by name. Full mechanics in
  archives/anonymization/RESTORE.md (local-only).
- Linear is written from the Cowork PM thread only. End sessions with a
  report-back block instead of touching the tracker.
- CHANGELOG [Unreleased] is a curated staging area. Release ceremony =
  RELEASING.md, and it includes publishing the GitHub Release
  (gh release create --notes-file "RELEASE NOTES vX.Y.Z.md"). RELEASING.md
  now carries that step explicitly -- it did not until MLB-154, which is
  how v1.6.0 got tagged and published with no notes file in the repo.
  The root carries exactly ONE notes file (the current release); the
  previous one moves to docs/releases/ as part of the cut.
- The repository ROOT is curated (MLB-154, 2026-08-02). Session exhaust
  never becomes a tracked root file again. Incoming artifacts land in a
  docs/ home instead: session handoffs, phase journals and progress notes
  -> docs/archive/ · design documents still in force -> docs/decisions/ ·
  shipped release notes -> docs/releases/. Working notes that are nobody
  else's business stay untracked (.gitignore has a root-anchored rider
  for the recurring names). If a new artifact does not fit one of those
  homes, ask -- do not default to root. CLAUDE.md itself stays at root:
  it is not documentation, it is the file Claude Code reads to load these
  rules, and it is only discovered at the root of the checkout.

## Kyle's question log (standing instruction, 2026-07-26)

When Kyle asks a conceptual question in chat, append ONE dated line to
"Study Material.MD" under "Kyle's question log" -- extremely brief, e.g.
"7/26 asked about impact of git not tracking a file". Do not answer in
the entry; name where context lives if obvious. Recurring themes get a
line in that section's "Recurring confusions" subsection.

Also standing for Study Material.MD: append a curveball-index line
whenever something non-obvious gets solved, and "why this approach"
notes worth retelling in an interview.
