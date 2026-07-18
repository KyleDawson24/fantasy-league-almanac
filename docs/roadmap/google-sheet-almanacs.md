# Google Sheet Almanacs

**Linear:** [Google Sheet Almanacs](https://linear.app/roguelitedevelopment/project/google-sheet-almanacs-d2df60725ae0) · Backlog · No priority

Home for the Google Sheet almanac surface, split along the axis that had
been causing terminology drift — "ESPN" and "CBS" each mean two different
things, so the project separates them explicitly.

**Platform-general** (the reusable renderers any league on that platform
inherits): **ESPN Platform Almanac** (`output/almanac_*`) and **CBS
Platform Almanac** (`output/cbs_almanac_sheets.py` + the shared
`almanac_logic`/`almanac_render` seams it reaches via the provider
pattern). Changes here apply to every league on the platform.

**League-specific** (the two almanacs we actually ship, each on its own
dev sheet): **Buns in the Sun Almanac** (`espn-main`) and **Box Score
Baseball Almanac** (`cbs-bsb`). These get **special treatment** — a
half-way-to-public production staging area — and their changes are
**often bespoke** to that league and deliberately NOT reflected
universally (e.g. the "*CBS does not avail draft data prior to 2025"
caveat is bsb-specific prose; the coverage years beside it are
data-driven). Kyle's eyeball of the dev sheet is the merge gate.

**Cross-cutting:** **Cross-Platform Google Sheets Almanac** — changes
that genuinely need to land on both platforms (the shared visual system,
the shared builders/helpers, structural-parity work). The Draft Recap
overhaul is the freshest example: leaderboards, the Top-Pick boards, and
season-pacing built to render identically on ESPN + CBS. The two writers
mirror rather than share (ESPN computes colors writer-side; CBS takes
builder-side specs) — parity is a discipline, not always shared code.

**Depends on:** Platform Abstraction (the platform-general seams) and
Multi-Platform Support (per-platform data). Overlaps neither: this is the
OUTPUT-surface axis, orthogonal to the capability projects.

**Seeded issues:** MLB-85 ESPN Platform Almanac · MLB-86 CBS Platform
Almanac · MLB-87 Buns in the Sun Almanac · MLB-88 Box Score Baseball
Almanac · MLB-89 Cross-Platform Google Sheets Almanac. (The Draft Recap
data plumbing's dbt cleanup is tracked separately as MLB-90 under Ops,
Debt & Hardening.)
