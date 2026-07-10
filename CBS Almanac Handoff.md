# CBS Almanac — Universal-Stats Pivot Handoff (2026-07-09)

Branch: `claude/modest-montalcini-3af8c4` (worktree `modest-montalcini-3af8c4`).
**Unpushed by design** — QA bar: nothing pushes until CBS outputs generate.
Run everything **from the worktree**; the raw data lives in the **main checkout**
(`C:\Users\kyled\projects\espn-league-manager\data\...`) — pass `--data-dir`.
Tooling: the `.venv` in the **main checkout** (`...\espn-league-manager\.venv\Scripts\`).

---

## TL;DR

The CBS almanac's **stats source pivoted** this session. The hard half — acquiring
and loading the data — is **done and verified in Snowflake**. What remains is pure
dbt modeling: recompute → rebuild the record book → render an almanac output.

- **Why the pivot:** CBS's `league/stats` API is **free-agent-only** — every
  currently-rostered player is absent from all 20 years of "history" (0/480). The
  first CBS record book was silently wrong (JD Martinez's 45 HR "record"; no Cole,
  Judge, Trout, Ohtani). Kyle caught it.
- **The fix:** source the **baseball layer** (what a player did, per game) from the
  free public **MLB Stats API** (`statsapi.mlb.com`), joined to CBS's **fantasy
  layer** (who was rostered/active when + scoring rules). Portable across platforms;
  complete (all MLB players). Spike-proven.

---

## The architecture (read this first)

**Two layers, cleanly split:**
- **Fantasy layer** (platform-specific): membership (who was rostered), active vs
  reserve, scoring rules. Only the platform knows this. Source: CBS.
- **Baseball layer** (universal): what each player did per game. One true answer.
  Source: MLB Stats API. Reused across CBS/Yahoo/Fantrax.

**Two scoring lenses** (the ESPN `platform_`/`calculated_` pattern, per Kyle):
- `calculated_` = universal stats × current CBS scoring rules. **The default.** The
  only per-game-capable lens (needed for active/reserve slicing + best-games).
- `platform_` = CBS's own awarded season FPTS. The reconciliation **anchor**
  (season-grain; CBS serves no per-game FPTS).
- The **delta is a feature** — surfaced and called out. It catches era-rule changes
  AND CBS data gaps (spike: Kirby Yates 2019 — CBS under-tracks inherited-runners-
  stranded, so `calculated_` is *more* accurate than CBS's own feed).

**Active-only is the default** (Kyle, explicit): production counts **only while
rostered AND active**. Cole striking out 326 but benched for a 10-K game = 316 in
this league. Requires **per-game stats × per-day active status**. Per-game we have;
active-status-per-day is 2026 (roster capture) + history (MLB-63 reconstruction).

---

## Data inventory — all in Snowflake `RAW` (`ESPN_FANTASY.RAW`)

| Table | Rows | Role |
|---|---|---|
| `MLB_GAMELOGS` | 592,729 | **The stats spine.** Per-game, universal, 2214 players |
| `MLB_SEASON_STATS` | 16,906 | Season aggregates (yearByYear); carries season `team` |
| `CBS_MLBAM_CROSSWALK` | 2,226 | CBS player id → MLBAM id (99.7%; 21 `_COLLISION` flagged) |
| `CBS_GAMELOGS` | 677,151 | CBS's own per-game (FA-only) — **reconciliation ground-truth** now |
| `CBS_SEASON_STATS` | 44 files | CBS season FPTS = the `platform_` anchor (FA-only) |
| `CBS_ROSTERS` | 105 | 2026 daily rosters (`roster_status` A/RS = active/reserve) |
| `CBS_STANDINGS` | 16 | 2026 period standings (drives the arc) |
| `CBS_CONFIG` | 15 | scoring_rules (the 16 scored categories + weights) |
| `CBS_TRANSACTIONS` | 3 | 2026 rolling window |

Seeds (`ANALYTICS`): `canonical_stats`, `cbs_stat_map`, `stat_classification`
(now carries a `canonical_key` bridge + an `IRSTR` row).

**No more extraction/loading is needed for the current scope.** (Exception: the UI
historical rosters, MLB-47 — captured as HTML under `data/cbs_raw/bsb/history/ui/`,
not yet parsed — feed departed-players + ownership, a later phase.)

---

## Extract scripts (`extract/`)

| Script | What | Notes |
|---|---|---|
| `mlb_crosswalk.py` | CBS player → MLBAM id | name+season+team; lands `CBS_MLBAM_CROSSWALK` |
| `mlb_stats.py` | universal per-player game logs | statsapi; idempotent/resumable; lands `data/mlb_stats/` |
| `mlb_load.py` | files → `MLB_*` raw tables | NDJSON+COPY, `--data-dir <main>/data/mlb_stats` |
| `cbs_backfill.py` | CBS season+gamelog archive | museum rule; done (now ground-truth) |
| `cbs_load.py` | CBS files → `raw.cbs_*` | `--data-dir <main>/data/cbs_raw` |

**Long sweeps MUST be launched DETACHED** (`Start-Process ... -WindowStyle Hidden`),
not via a Bash background shell — those die when the Claude Code process exits (it
killed the MLB extract once at 87/2214). Always make sweeps idempotent + log to disk.

---

## dbt models built this session (`dbt_league/models/`)

**Good / keep:**
- `staging/stg_cbs__standings` → `marts/reporting/mart_period_standings` — the 2026
  standings arc. Real, verified (Baltic White Sox's pennant race). Platform-neutral.
- `marts/core/fct_team_season_performance` → `marts/reporting/mart_team_alltime` —
  the shared, **format-modular** team-stats spine + all-time ESPN team stats (W-L a
  format-conditional overlay). Verified vs `mart_team_season_standings`.
- The **vocab bridge**: `stat_classification.canonical_key` (67 ESPN stats mapped) +
  an `IRSTR` row; `dim_stat` exposes `canonical_key`. Byte-neutral for ESPN.

**Superseded (rebuild on universal stats):**
- `staging/stg_cbs__player_season_stats` → `marts/reporting/mart_player_season_records`
  — the record book, but built on the **FA-only** CBS universe. Wrong population.
  Rebuild against `MLB_GAMELOGS` (`calculated_`).

---

## What's left — the chain to a working almanac output

1. **Collision-fix** (21 rows). The crosswalk flagged 21 same-name collisions (two
   real Max Muncys / Will Smiths, three Luis Garcías) `_COLLISION`. Split them with
   **season-team**: match CBS `pro_team`-per-season to the MLBAM's team-per-season
   (`MLB_SEASON_STATS.team` — verify it's populated; the per-game `game:team` came
   back null). Rewrite those crosswalk rows.

2. **Recompute (MLB-62)** — the core dbt build:
   - `stg_mlb__player_game`: `MLB_GAMELOGS` → tidy per-game stat rows. Map statsapi
     keys (`strikeOuts`, `homeRuns`, `earnedRuns`, `inheritedRunners`,
     `inheritedRunnersScored`, `outs`, `wins`, `saves`, `holds`, `completeGames`,
     `hits`, `baseOnBalls`…) to the shared vocabulary (build an `mlb_stat_map` seed
     like `cbs_stat_map`, or map straight to `canonical_key`).
   - Per-game `calculated_` FPTS × CBS scoring rules (`stg_cbs__scoring_settings` from
     `cbs_stat_map.points_2026`). **Gotchas baked into the spike:** INN is scored at
     **out-granularity** (+1/out via `outs`, NOT +3/inning); **QS** is derived
     per-start (`gamesStarted=1 AND IP≥6 AND ER≤3`); **IRSTR** =
     `inheritedRunners − inheritedRunnersScored`.
   - Reconcile season-sum(`calculated_`) vs CBS `platform_` (season universe FPTS,
     where available) → the delta report (era-rule + data-gap detection).

3. **Rebuild the record book**: `mart_player_season_records` on `calculated_` from the
   universal source → Cole 326 K, Judge 62 HR, the real thing. **Active-only** lights
   up when membership (MLB-63) lands; until then, ship the "total (rostered)" lens
   clearly labeled.

4. **Render (MLB-66 / MLB-58)**: the almanac Sheet (home + team tabs) via the
   registry-resolved sink into Kyle's Sheet (MLB-49, created). Reuse the ESPN almanac
   write/format plumbing; content assembly is format-conditional.

---

## Tickets (Linear team `fantasy-league-almanac`, project Multi-Platform Support)

- **MLB-70** — universal stats extract + crosswalk. Crosswalk + extract + load DONE;
  the recompute-feed is what remains. (AlmanacAgent)
- **MLB-62** — per-game FPTS recompute. Reframed to universal-sourced; the next build.
- **MLB-61** — CBS staging. Standings arc + FA-only record book landed; pivot to
  universal for the record book.
- **MLB-63** — ownership/active-set reconstruction. **Now the spine** (production only
  counts while rostered/active). Fed by MLB-47 UI rosters + transactions.
- **MLB-65 / MLB-66** — marts / almanac v1 (the render).
- **MLB-45** — CBS backfill. Done; recontextualized as reconciliation ground-truth.
- **MLB-68** — Ohtani split: DECIDED (report as two players).
- **Kyle's side:** MLB-49 (Sheet — done), MLB-50 (recap scope), MLB-51 (lore), MLB-52
  (rollover ~9/20), MLB-57 (review checklist).

---

## Gotchas / lessons (don't re-learn these)

- **FA-only universe** is the whole reason for the pivot — don't trust CBS
  `league/stats` as a historical record. (`player_status=all&owned=all` unlocks the
  *current* pool but it's still "current pool viewed historically.")
- **Verify on-branch before every commit** — this session's second half was
  committed on a **detached HEAD** and had to be reclaimed by fast-forward. Run
  `git symbolic-ref -q HEAD`.
- **Detached sweeps** (above). Keep the laptop awake for long runs.
- **Crosswalk is name-based**; the 21 collisions need season-team.
- **statsapi shape:** `people/{id}/stats?stats=gameLog&group={hitting|pitching}&season=YYYY`;
  splits are games; season stats via `stats=yearByYear&group=hitting,pitching`;
  name→id via `people/search?names=`; all-players-in-a-season via
  `sports/1/players?season=YYYY`. Free, no key.
- **Reconciliation, not exact-match:** `calculated_` won't always equal `platform_`
  (CBS data gaps) — that's the point.
