# Multi-Platform Support

**Linear:** [Multi-Platform Support](https://linear.app/roguelitedevelopment/project/multi-platform-support-aaaa5e9aa79f) · Backlog · Medium

Per-platform rollouts once Platform Abstraction defines the contract. One
Linear milestone per platform (CBS · Yahoo · Platform #3 pending recon), each
starting with an access spike — what can this login actually see? — before
any code.

Data assets in hand: a login to a 20-year CBS league (father-in-law's;
archive depth and keeper status unknown — if the history is accessible it is
a spectacular test corpus) and a dormant 4-team Yahoo dummy league (messy is
fine for API-shape recon). Platform-landscape recon runs first: verify
whether Sleeper even offers season-long MLB (it is football-first; baseball
support is the open question) and assess Fantrax (the deep/dynasty baseball
standard) and Ottoneu before committing the #3 slot.

## Recon findings (2026-07-07, MLB-12)

| Platform | Season-long MLB? | Data access | Auth |
|---|---|---|---|
| ESPN | ✓ (current adapter) | unofficial (`espn-api` + raw views) | session cookies |
| Yahoo | ✓ (#2 mainstream) | official, documented Fantasy Sports API | OAuth2 app |
| CBS | ✓ (paid commissioner OG, deep archives) | official Developer API v3.0; community token tooling | league-scoped token |
| Fantrax | ✓ (deep/dynasty standard, ~1M+ claimed) | community access by league ID (`fantraxapi`-class libs) | league ID (+ session) |
| Sleeper | **✗ — DFS picks only; no season-long MLB** | n/a for baseball | — |
| Ottoneu | ✓ (salary-cap niche) | limited/exports | — |

Decided (2026-07-07): Sleeper off the board; integration order is
**CBS → Yahoo → Fantrax** (milestone renamed), mainstream first, with
each go/no-go still made individually at its access spike. Ottoneu only
on user demand.

**CBS spike: DONE, verdict GO** (MLB-13). The reference league ("Box
Score Baseball", 16 teams, pure points, no keepers, fielding scored)
exposes real season-grain player history (sparse 2004–2010, full
2020+), while daily stats / rosters / standings / transactions are
current-season only — so the adapter backfills season grain and
forward-captures daily from 2026-07 on. Auth is a browser-extracted
token (login is reCAPTCHA-walled). The non-H2H format spawned the
league-format abstraction workstream (MLB-43); full capability manifest
on the ticket and in the adapter contract's living notes.

**CBS product plan (Per Offline Chat, 2026-07-07):** the manifest splits
the league's data into a real 20-year *player* archive and a perishable
*owner* layer that exists only for 2026 — the product follows that
split. Approved order: **(1)** MLB-44 fantasy-layer capture (landing:
gitignored raw JSON at `data/cbs_raw/` in the main checkout; Snowflake
can load the files later) → **(2)** MLB-45 gamelog+season backfill →
**(3)** MLB-43 first slice on 2026 data (points standings, weekly
movement, bench efficiency — computable for 2026+ only, since deployed
slots are captured in-season; upgraded 2026-07-07: CBS's own
period-end standings history turned out to be servable via
`period=N` and is captured outright, so movement views can read
official standings instead of recomputing) → **(4)** the "20 Years of Box Score
Baseball" retrospective (MLB-46), an end-of-season league deliverable
built from the backfill (player-division records under the league's own
scoring; owner history only via a manually-seeded league-lore file,
since the API cannot recover historical standings). Ruled out by the
manifest: historical wasted-points/optimal-lineup, owner-attributed
historical records, draft surfaces, transactions beyond the rolling
window. (Softened 2026-07-08: the site UI's league-history pages carry
**year-end rosters, transactions, and drafts back to at least 2021**
— owner names included — that the API denies under every probed param;
MLB-47 chases the page sources and capture. For 2021+ that means exact
ownership reconstruction — year-end roster walked backwards through
the season's transaction log — plus a recent-era draft lens, with
per-year completeness verified by content. Only deployed slots /
started-sat stay unknowable pre-2026.) Decided 2026-07-08: 2026-forward surfaces are ACTIVE-ONLY
(deployed-lineup crediting, as on ESPN) with explainers wherever eras
mix — the started/sat signal is the value that accrues as seasons
stack.

**CBS build-out plan (itemized 2026-07-08):** the full chain from the
captured raw archives to output equivalent to the ESPN production
surfaces, decomposed under the MLB-43 epic. Foundation first (Platform
Abstraction): MLB-48 league registry + run-targeting design (ACCEPTED
2026-07-08: one warehouse namespace, `league_key` in every grain —
per-league schemas rejected; see the contract's multi-league appendix)
→ MLB-57 league-scoped runs (the `league_key` re-grain, ESPN as
byte-neutral entry #1, goldens-gated) → MLB-58 per-league output
sinks ("run this league, write to this sheet" — the shareability
dry-run). Data chain (CBS milestone): MLB-59 warehouse loader →
MLB-53/54/55/56 UI parsers (standings / transaction verb census /
year-end rosters / drafts+franchise overviews; synthetic test fixtures
only — the repo is public) → MLB-60 stat crosswalk (fielding included)
→ MLB-61 staging onto the accepted contract → MLB-62 per-game FPTS
recompute reconciled to season anchors (residuals double as era-rule
detection) → MLB-63 ownership + active-set reconstruction with
per-season fidelity grades (the owner-story centerpiece) → MLB-64
dim_franchise (rename-proof continuity; v1 may ship without).
Surfaces: MLB-65 marts (standings arcs, record book with the
2026+-active-only era rule, the first-ever champions list) → MLB-66
almanac v1 (home + team tabs, explainers required) → MLB-46
retrospective. Maintainer-side: MLB-49 create the almanac Sheet ·
MLB-50 recap-surface scope decision · MLB-51 lore file · MLB-52
rollover checklist (due ~2026-09-20).

**Depends on:** Platform Abstraction (the contract); access spikes can run
anytime.

**Seeded issues:** MLB-12 platform recon (done — findings above) ·
MLB-13 CBS access spike (done — verdict GO) · MLB-44 2026 fantasy-layer
capture (season-to-date landed + content-verified 2026-07-07 — 105
roster dates, 16 standings periods, full 197-entry transaction window;
cadence decided Per Offline Chat 2026-07-07: rides the ESPN weekly
runbook as its last step — SETUP.md documents it, and the MLB-31
runner inherits it as a non-fatal step) · MLB-45
gamelog backfill (2004–2025 COMPLETE + verified 2026-07-07: 3,809
player-season gamelogs, 237,181 player-games, one evidenced tombstone;
2026 sweeps at rollover) · MLB-46 retrospective (end
of season) · MLB-47 UI league-history capture (CAPTURED + verified 2026-07-08:
526 pages, verdict PASS — standings 2001–2026, transactions ×2
filters 2001–2026 with Activated/Reserved moves present back to 2001,
roster reports 2003–2025 at per-year team counts (12–19 by era),
drafts 2017+, 34 franchise overviews; franchise ids stable across
renames so the continuity join works; the live season's roster pages
render differently and are owned by the MLB-44 API capture; the
HTML→structured parse layer rides MLB-43) · MLB-49 almanac Sheet
(maintainer) · MLB-50 recap scope decision (maintainer) · MLB-51 lore
file (maintainer) · MLB-52 rollover checklist (due ~2026-09-20) ·
MLB-53..56 UI parsers · MLB-59 warehouse loader (API-JSON half LANDED
2026-07-08: `extract/cbs_load.py`, six `raw.cbs_*` tables with
league_key + envelope lineage — 105 roster dates, 16 periods, 3
transaction snapshots, 15 config snapshots, 22 season-stat files,
556,493 verbatim per-game gamelog rows from all 3,809 player-season
files; idempotency + spot checks proven; UI-rows half blocked on the
MLB-53..56 parsers) · MLB-60 stat
crosswalk (LANDED 2026-07-08, in review: `canonical_stats` +
`cbs_stat_map` seeds per the accepted MLB-4 design — canonical keys
project-owned, bref_key as nullable alignment; census findings: 2026
rules score NO fielding, and **the captured feeds contain NO pitching
stats — MLB-45 reopened for a pitching-capture variant, gating
MLB-62**) · MLB-61 staging · MLB-62 FPTS recompute · MLB-63
reconstruction · MLB-64 dim_franchise · MLB-65 marts · MLB-66 almanac
v1 · MLB-14 Yahoo access spike ·
MLB-42 Fantrax access spike (needs a dummy league)
