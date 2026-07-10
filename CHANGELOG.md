# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the corresponding `Phase X.Y Documentation.md` in the
repository root for the architectural detail behind the change.

## [Unreleased]

Portfolio-readability pass over the dbt project: a layered DAG, enforced
data-quality tests, and accurate exposures — all byte-neutral for the
three output surfaces (recap, records report, almanac). Plus the first
v2.0 feature: the almanac's Advanced Standings tab rebuilt as per-stat
weekly-average standings over two new reporting marts (intentionally
output-changing; the almanac goldens were re-anchored under review).
And the multi-league foundation (MLB-48/MLB-57): a league registry plus
the `league_key` re-grain of every warehouse layer, held byte-neutral
for the ESPN league.

### Added

- **Universal stats layer: the CBS record book's stats source pivoted to the
  MLB Stats API (MLB-70).** CBS's `league/stats` history is free-agent-only —
  every currently-rostered player is absent from all 20 "historical" years
  (verified by content: universe ∩ rosters = ∅), so a record book built on it
  silently lacked Cole, Judge, Trout, and Ohtani. Player production is now
  sourced from the public statsapi.mlb.com (complete for all MLB players,
  portable across platforms) and joined to CBS's fantasy layer (membership +
  scoring rules); the CBS gamelog archive is recontextualized as
  reconciliation ground-truth for the `platform_` lens.
  - **`extract/mlb_crosswalk.py`** — CBS player id → MLBAM id (2,225 rows,
    99.7%). Name-normalized matching disambiguated by season overlap AND
    season-team agreement: CBS's per-season `TM` column against the statsapi
    season listings' `currentTeam` (verified season-accurate), with the CBS
    team-code map *learned* from unique-name co-occurrence (31 codes, 3×
    dominance guard) rather than hardcoded. Fuzzy initial-key matches are
    rejected when team evidence disagrees in every comparable season. The
    team pass resolved all 21 flagged same-name collisions (three Luis
    Garcías, two Will Smiths, two Max Muncys…) and caught three unflagged
    silent mismatches — Vladimir Guerrero **Jr.** had been mapped to his
    father, Eury Pérez to a 2012 outfielder of the same name, Juan Morillo
    to a 2006 reliever. Content-verified: Vladdy Jr 2021 HR=48, catcher
    Will Smith 2024 HR=20, Astros Luis García 2021 W=11, Eury 2023 K=108.
  - **`extract/mlb_stats.py`** — season (yearByYear) + per-game (gameLog)
    sweeps for every crosswalked MLBAM id (idempotent/resumable, polite
    pacing, no key needed). 2,227 players, 14,518 gamelog-season files,
    zero failed fetches.
  - **`extract/mlb_load.py`** — lands the files verbatim in two
    platform-neutral raw tables (deliberately no `league_key`: this is the
    shared baseball layer every league joins to): `RAW.MLB_SEASON_STATS`
    (16,957 rows) and `RAW.MLB_GAMELOGS` (595,918 per-game rows,
    1991–2026). Content-verified: Cole 2019 K=326, Judge 2022 HR=62 — the
    totals the free-agent-only source could never produce.

- **Transaction Records: production by acquisition channel on Advanced
  Standings (MLB-16 spike / MLB-17).** The named key output — team rankings
  by how each player's production was acquired.
  - **The ESPN transaction log, found by content (MLB-16).** The durable,
    full-season add/drop/trade log lives on the league *message board* — the
    `communication/?view=kona_league_communication` endpoint's
    `ACTIVITY_TRANSACTIONS` topics, paged to exhaustion — NOT in `mTransactions2`,
    which for `flb` is a current-scoring-period decoy (200 OK, ~40 rows, no
    filter widens it). Verified 3,028 topics spanning draft day → today.
    `extract/extract.py` gains `--include-transactions` / `--transactions-only`,
    landing the verbatim topics in a new append-only `RAW.TRANSACTIONS`
    (league_key-stamped, `ADD COLUMN IF NOT EXISTS` self-heal, dbt source +
    migration list updated). Current-season only for now (prior seasons 404 the
    per-season path; `leagueHistory`'s communication view rejects the topics
    filter — a documented follow-up).
  - **`stg_transactions`** decodes the messageTypeId vocabulary (178 add / 179
    drop / 224·239·244 trade legs / 188 lineup-noise dropped) into a
    platform-neutral directed-event shape (a NULL team side = free agency) that
    a future `stg_cbs__transactions` converges onto.
  - **`fct_roster_stints`** (marts/core): one row per contiguous window a player
    spent on a team, tagged with how it opened (KEEPER / DRAFT / TRADE / FA_ADD)
    and closed (DROPPED / TRADED_AWAY). Membership is the DENSE lineup shell
    (`stg_box_scores`, gaps-and-islands over a per-league dense period index, so
    an unloaded All-Star gap doesn't split a stint while a real off-roster gap
    does); the log supplies the directed 224/244 TRADE edges — the only thing
    roster state can't tell from a same-window drop+add. Draft/keeper from
    `stg_draft`; scoped to seasons that have a transaction log so no season
    silently mislabels team-changes as adds. Locked stint semantics (Per Offline
    Chat 2026-07-09): most-recent event governs, no channel inheritance, the
    lost-clock keyed on player so the thrice-dropped guy isn't double-counted.
  - **`mart_team_acquisition_channels`** (marts/reporting): wide per-team, two
    lenses — ACTIVE (started points; lost = for other teams) and ROSTERED (all
    points incl. bench/IL; lost = other teams AND unowned) — with FA and Trade
    Net deltas. Reconciles exactly: the four acquired channels sum to each
    team's own active (and rostered) production.
  - **Advanced Standings** grows two stacked blocks under the weekly grid
    (Active + Rostered lenses), teams as rows ranked by Acquired total, with
    write-layer gradients (acquired green-high, lost green-low, the Nets
    zero-centered diverging / polarity-aware). The almanac golden re-anchored on
    `Advanced-Standings.tsv` ONLY — a pure append; the recap and records BBCode
    goldens held byte-identical. On the `league_almanac` exposure; grain-tested;
    full `dbt build` green (337 nodes), 7 new almanac unit tests.

- **CBS player record book — the first tangible CBS almanac content
  (MLB-61 F1 / MLB-65), and the vocabulary bridge under it.** The
  ESPN→canonical bridge (`stat_classification.canonical_key` + an `IRSTR`
  row, exposed on `dim_stat`, byte-neutral for ESPN) lets CBS converge on
  the stat_names the DAG already speaks. `stg_cbs__player_season_stats`
  unpivots `CBS_SEASON_STATS` through it — 252,224 stat-rows across 8,926
  player-seasons (2004–2025), season FPTS as `PLATFORM_POINTS`, innings
  via `OUTS`, the scored strand as `IRSTR`. `mart_player_season_records`
  ranks the top-10 single-season performances all-time per stat, reusing
  the shared `dim_stat` catalog for candidacy/polarity/display (CBS
  records need zero CBS-specific stat metadata). Content-verified against
  real MLB history: best season ever = Verlander 2011 (1,010 pts), most K
  = Kershaw 2015 (301), most HR = J.D. Martinez 2017 (45). No recompute
  needed for the platform lens (season FPTS is in the data); the
  calculated lens + best single *games* follow with the gamelog recompute
  (MLB-62). 18 schema tests + full suite green.

- **Shared format-modular team-season fact + all-time ESPN team stats
  (MLB-69).** `fct_team_season_performance` is the season-grain team-stats
  spine both platforms feed: the stat rollup is *format-agnostic* (sums
  the player-active fact, so any format produces team stats once players
  carry a `team_id`), while the W-L / authoritative-platform-total
  *overlay* is *format-conditional* — a LEFT JOIN of the matchup-gated
  team-week fact, populated where the league delivers matchups (H2H, any
  platform) and NULL where it doesn't (points, any platform). The toggle
  is data-presence, never a platform check; the playoff filter is
  NULL-safe so a no-schedule league isn't silently dropped. Season is the
  grain atom — all-time is a rollup, so the same fact serves single-season
  and all-time. Verified against `mart_team_season_standings`: counting +
  W-L match exactly on all 30 ESPN team-seasons (calculated points within
  0.4, this fact being the more-correct round-once-at-season). On top,
  `mart_team_alltime` rolls it into franchise records (all-time
  accumulation + best single season) — the long-wanted all-time ESPN
  team stats, with league-wide all-time wins == losses (282-282)
  confirming the overlay. Both models additive; no shared ESPN model
  changed. CBS team stats drop in unchanged once the player-performance
  convergence lands.

- **First CBS reporting mart: the 2026 standings arc (MLB-61 F7).** CBS
  becomes adapter #2 at the staging boundary, starting with the
  lowest-risk feed. `stg_cbs__standings` reads `raw.cbs_standings`
  directly (F7 standings are *platform-delivered* — a non-H2H points
  league has no matchups to derive standings from), and
  `mart_period_standings` computes the arc: one row per
  `(league_key, season_year, period, team_id)` with cumulative points +
  rank and derived movement (points earned that period, rank change,
  distance behind the leader). Platform-neutral by name and shape — any
  future points league reuses it. The six `raw.cbs_*` tables are declared
  as dbt sources. No shared ESPN DAG model changed (zero goldens risk);
  256 rows, 19 schema tests + the full 194-test suite green.
  Content-verified against the real 2026 pennant race. Scope: 2026
  in-progress only (the API is current-season); historical champions
  land from the parsed UI pages (MLB-53) into the same mart shape.

- **League registry + `league_key` re-grain (multi-league foundation,
  MLB-48 design / MLB-57 implementation).** One warehouse namespace,
  `league_key` as a first-class dimension in every grain — the accepted
  alternative to per-league schemas (consumers say "go to the mart and
  pull the league's data," never "pull CBS from the CBS mart").
  - `config/leagues.yml` + `config/league_registry.py`: one entry per
    league (platform, env-referenced league id, credential list, seasons,
    sinks — no format fields; format stays settings-derived at staging).
    `espn-main` is entry #1 and the default everywhere, so the weekly
    runbook is unchanged; `cbs-bsb` is the read-only museum entry. Loud
    failures: unknown keys list the known ones, missing credentials name
    the exact `.env` variables.
  - Extract stamps `league_key` into every RAW row (payloads stay
    verbatim — it's load metadata); tables self-heal the column via
    `ADD COLUMN IF NOT EXISTS`; the box-score idempotency DELETE is
    league-scoped; `tools/migrate_raw_league_key.py` backfilled the 312
    pre-registry rows. `--league` flag on the extract, which validates
    it was pointed at an espn-platform league before touching anything.
  - dbt: all staging models emit `league_key` (latest-snapshot windows
    and "current season" resolve per league); every join carrying
    team/player coordinates widened (team_id 1 in two leagues is two
    teams; each league's scoring weights apply only to its own rows);
    every `unique_combination_of_columns` test and incremental
    `unique_key` widened; the three incremental facts filter through a
    new `league_period_watermark` macro (per-league watermarks, so one
    league mid-backfill can't be skipped because another sits at the
    current week). Leaderboard rankings, league averages, and
    weeks-in-history percentiles all partition per league. Seed-derived
    dims (`dim_stat`, `dim_matchup_period`) stay unscoped until the
    crosswalk/schedule workstreams (MLB-4/MLB-5). One `dbt build` still
    builds every league — no per-league vars or targets.
  - Output layer: a process-wide league context in `output/db.py`
    (`set_league()` / `league_predicate()`); every league-scoped query
    across the recap, records report, season report, league notes, and
    almanac filters the active league, including whole-fact CTE legs and
    MAX(season) bootstraps; joins between scoped surfaces add
    `league_key` to their join keys. `--league` on all four render
    scripts (the weekly recap gained its first argparse), defaulting to
    the registry default.
  - Migration gate: RAW backfill + full-refresh rebuild, then the golden
    suite. Both BBCode goldens held **byte-identical**. The almanac TSVs
    moved on exactly 63 cells, every one a `ROUND(SUM(float),1)`
    .x5-boundary re-roll from the rebuild's new summation order
    (verified at the warehouse: e.g. an opponent total sitting at
    exactly 216.25) — re-anchored under review, same phenomenon that
    originally pinned the score-sum marts to tables.

- **CBS raw archives loaded into the warehouse (MLB-59, API-JSON
  half).** `extract/cbs_load.py` lands `data/cbs_raw/bsb/` as six
  `raw.cbs_*` tables — rosters, standings periods, transaction and
  config snapshots, season stats, and per-game gamelog rows (556,493
  verbatim game objects from all 3,809 player-season files, empty
  captures represented by sentinel rows) — every row carrying
  `league_key` plus envelope lineage (endpoint, params, captured_at,
  source_path). Staged-NDJSON `COPY INTO` mechanics, idempotent by
  source path, `--dry-run`/`--force`/`--families` flags; payloads stay
  untouched VARIANTs (staging owns interpretation). The parsed-UI half
  of the loader waits on the HTML parsers.

- **CBS pitching archive swept + crosswalked (MLB-45 reopened / MLB-60).**
  The historical archive was hitters-only because the universe query was:
  `league/stats` serves the hitter table by default, and `position=P` is
  the pitcher-universe key (the `stats_type=pitching`-style toggles all
  decoy — empty 200 or the hitter default, failing silently). `extract/
  cbs_backfill.py` gained a `--backfill-pitching` mode that sweeps
  per-season pitcher universes under that param (validated against the
  2025 anchor before trusting any sparse-era emptiness) and their
  gamelogs under the same content-authenticity gate as the hitter sweep.
  - Landed 2026-07-09: **22 pitcher-universe season files (5,116
    player-season rows), 5,109 pitcher-season gamelogs (2007–2025),
    ~120,700 per-appearance rows**, verdict PASS. Pitcher gamelogs are
    appearance-grain (only games pitched), unlike the schedule-grain
    hitter logs.
  - The two-way check turned up a structural fact: CBS models a two-way
    player as **two separately-rosterable pseudo-players** under sentinel
    ids (900 "Ohtani (Batter)" / 901 "(Pitcher)", on different teams in
    2026), which `league/stats` omits from both universe tables — so the
    person was invisible to the whole 20-year archive. `players/gamelog`
    serves the ids directly, so the sweep fetches them explicitly (901's
    2021 log = his real 23 starts; 2020 = 2, the injury year).
  - The pre-2007 era serves no per-game data for anyone (hitters empty,
    pitchers HTTP 500); 7 star pitcher-seasons tombstoned as
    `KNOWN_UNAVAILABLE` with two-run evidence.
  - `extract/cbs_load.py`'s season-stats walker now also walks the
    `stats_pitching/` directory (both universes share `CBS_SEASON_STATS`,
    told apart by `params.position`); reloaded idempotently —
    `CBS_SEASON_STATS` 44 rows, `CBS_GAMELOGS` 677,151. Warehouse
    content-verified: an in-universe ace's gamelog matches file-for-file,
    and **2025 season FPTS reconciles 594/594 exact** under the recompute
    formula (the MLB-62 anchor). Read-only throughout (museum rule).

- **Canonical stat catalog + CBS crosswalk (MLB-4 design / MLB-60).**
  `canonical_stats` seed: 56 project-owned stat slugs (fielding
  first-class) with Baseball-Reference alignment as a nullable
  `bref_key` column — populated only for stats with a real-world
  identity, honest NULLs for fantasy constructs (QS, holds, IRSTR).
  `cbs_stat_map` seed: full-vocabulary accounting of every key in the
  loaded CBS archives plus all 16 scored categories, dispositioned
  mapped / metadata / derived_composite / vestigial / unknown, with
  scored-coverage enforced by dbt + unit tests against a committed
  scoring-rules fixture. The census behind it surfaced that the current
  CBS rules score NO fielding (vocabulary ≠ rules), and — at the time —
  that the captured feeds held no pitching stats, which reopened the
  backfill ticket (MLB-45) for a pitching sweep. That sweep has since
  landed (next entry), and the crosswalk now carries the full pitching
  vocabulary.

### Changed

- **Advanced Standings tab reworked (v2.0 feature #1).** The standings
  block now shows each scored stat individually — the same seed-driven
  stat set and order as Matchup History — plus the Offense / Defense /
  Total / Against points columns, with every value a per-standard-matchup
  average: `value * standard_matchup_days / scoring_days_played`, where
  gameplay days are scoring periods rather than calendar days (the
  14-calendar-day All-Star week counts its ~11 game days) and the
  standard matchup length is derived per season (modal regular-week
  length — 7 here — so a 2-week-matchup league would normalize per-14
  with no code change). Raw season totals left the block; the weekly
  shape is how the league actually reads scores. IP renders as a base-10
  decimal (thirds notation doesn't survive averaging). The Points by
  Lineup Slot grid keeps season totals but drops BE / IL (a future
  bench/IL view belongs on the inactive-points lens), and its column
  order now comes from `dim_roster_slot_counts.sort_order` instead of a
  hardcoded Python map. Write-layer column gradients are polarity-aware
  per stat (negative-weighted stats like L / ER / BLSV paint green-low),
  positioned structurally rather than by header label since several
  abbrevs (K / BB / H / HR / R) repeat across the hitting and pitching
  blocks. Both blocks read the new marts; the almanac's two inline
  standings aggregations were deleted from `output/almanac_data.py`.
  Post-review polish: the slot grid is indented one cell with an Owner
  column added so its Team / Owner columns sit directly under Table A's;
  column widths are set by column type derived from the header layout
  (identity 52px, Owner 125px, value columns 40px, buffers 25px) rather
  than hardcoded letters; and none of the write-layer requests touch
  column visibility, so manually hidden columns (a stat the league
  never records, e.g. NH / PG) survive reruns — the tab is
  clear-and-rewrite, never delete-and-recreate.

- **marts/ re-layered into `marts/core` + `marts/reporting`.** The 4 dims
  and 7 facts (the contract layer) now live under `marts/core/`; the five
  consumer `mart_*` models under `marts/reporting/`. Pure file moves — no
  relation names, configs, or compiled SQL changed. The v1.2-era
  `_owner_models.yml` is folded into the per-directory schema files.
- **`analyses/check_*.sql` converted to singular tests.** The three
  assertion-shaped checks now run on every `dbt build` as
  `tests/assert_*` (season-rollup fidelity, full-partition, BE/IL
  eligibility leak) plus a severity-warn data-canary test (Trout / Soto /
  FA presence). The exploratory eligible-slots profile was deleted. The
  season-rollup check needed rewriting when enforced: the old analysis
  predated the season fact's round-once-per-row float freeze and no
  longer described the model's contract.
- **Exposures trued up.** `league_almanac` now declares its real reads
  (`mart_draft_board`, `int_player_position_pts`,
  `int_team_owner_display`, `stg_scoring_settings`) and drops the unread
  benchmarks mart; the below-core dependencies are documented on the
  exposure instead of hidden.
- **Weekly facts renamed to the entity-first scheme.** Every fact now
  reads `fct_<entity>_<grain>_...`: `fct_weekly_player_performance` →
  `fct_player_weekly_slot_performance` (the `_slot_` marker also fixes
  the long-standing grain-misleading name), `fct_weekly_player_active/
  inactive_performance` → `fct_player_weekly_active/inactive_performance`,
  and `fct_weekly_team_active/inactive_performance` →
  `fct_team_weekly_active/inactive_performance`. Warehouse relation
  renames; all dbt refs, exposures, singular tests, and ~45 Python query
  references updated in the same commit.
- **Season-grain float sums frozen.** `fct_player_season_performance`
  and `mart_team_matchup` are tables now -- as views their per-query
  float re-summation could flip .x5-boundary values between two reads
  with no data change. Regens between builds are now deterministic.
- **"Only staging reads sources" is now absolute.** The matchup-grain
  extraction that lived inside `fct_team_weekly_active_performance`
  moved verbatim into `stg_matchup_scores` (final team score per
  matchup) and `stg_matchup_pairs` (the who-played-whom spine), and
  `dim_roster_slot_counts`'s raw flatten moved into a long-form
  `stg_roster_settings`. A multi-grain source now feeds one staging
  model per grain. Proven equivalent: symmetric EXCEPT between the old
  inline SQL and the new staging views returned zero rows in both
  directions, and the team fact's deterministic-field hash and the
  roster dim's full-row hash are byte-identical pre/post.
- **The two consumer-contract intermediates promoted into core.**
  `int_player_position_pts` → `fct_player_position_pts` and
  `int_team_owner_display` → `dim_team_owner`: both were already
  consumer contracts in practice (the almanac reads them directly; four
  marts join the owner bridge), which per the v1.1.0
  `fct_player_daily_performance` precedent means they belong in
  `marts/core` with layer-correct names. Warehouse relation renames; the
  almanac's two queries updated in the same commit. All renamed
  relations' predecessors are left standing so an un-merged checkout
  keeps working — after this line merges and a `dbt build` has run from
  it, drop the seven orphans:
  `DROP TABLE ESPN_FANTASY.ANALYTICS.INT_PLAYER_POSITION_PTS;`
  `DROP VIEW ESPN_FANTASY.ANALYTICS.INT_TEAM_OWNER_DISPLAY;`
  `DROP TABLE ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_PLAYER_PERFORMANCE;`
  `DROP TABLE ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_PLAYER_ACTIVE_PERFORMANCE;`
  `DROP TABLE ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_PLAYER_INACTIVE_PERFORMANCE;`
  `DROP TABLE ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_TEAM_ACTIVE_PERFORMANCE;`
  `DROP TABLE ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_TEAM_INACTIVE_PERFORMANCE;`
- **Docs refreshed where stale.** dbt project README rewritten as a
  layer-by-layer architecture narrative; `dbt_project.yml` starter
  boilerplate replaced with purposeful comments; stale claims fixed
  (dim_stat's UNPIVOT-source note, owner_nicknames' "not joined yet",
  int_player_daily references in `generate_summary.py` comments); model
  counts corrected in the root README / docs overview; HANDOFF's model
  catalog brought current.

### Added

- **CBS 2026 fantasy-layer capture** (`extract/cbs_capture.py`, MLB-44):
  read-only preservation of the perishable owner layer of the CBS
  points league before season rollover — rosters for every season date
  with the deployed slot (`roster_pos`) and the started/sat split
  (`roster_status` A/RS), period-end standings, transaction-log and
  league/scoring-config snapshots. GET-only endpoint whitelist enforced
  in code (the museum rule), polite pacing with backoff, token never
  logged — and content-based verification that caught two decoys on the
  first runs. Rosters: the obvious `date` parameter answers HTTP 200
  with the *current* roster dressed in date-varying news headlines —
  byte-distinct payloads, zero history (105 dates, 2 distinct payloads,
  0 membership changes) — real history runs on `point=YYYYMMDD`, which
  maps dates to scoring periods; discovery accepts a parameter only
  when roster *membership* changes across two past dates, and the
  landed sweep cross-checks clean against transaction-log ground truth
  (624/624 adds/drops/trades consistent with day-before/day-of
  membership). Standings: `point` is the decoy there (echoes a period
  label over current totals; 105 dates, 1 distinct state) — real
  history uses the scoring-period NUMBER `period=N`, and every landed
  file must echo the period it was asked for (16/16 periods, mutually
  distinct, totals growing 94→4,795-style with first≤last asserted;
  strict monotonicity deliberately not — negative-scoring stats can
  shrink a total across one bad period). Full 16-team coverage asserted
  everywhere (`league/rosters` silently scopes to the token's own team
  without `team_id=all` — 1 team / 30 `roster_pos` vs 16 / 480).
  Transactions read from `league/transaction-list/log` (plain
  `league/transactions` 404s here); the first snapshot caught the full
  20260325→20260706 window (197 entries) before the rolling cap starts
  eating it. Lands raw append-only JSON envelopes under gitignored
  `data/cbs_raw/`; adapter-shaped staging comes later with the
  format-abstraction work. Weekly cadence: the capture rides the ESPN
  weekly runbook as its last step (SETUP.md) — idempotent, and
  positioned so a CBS token expiry can never block the ESPN update.
- **CBS historical backfill** (`extract/cbs_backfill.py`, MLB-45): the
  real half of the 20-year archive — per-season player universes from
  `league/stats?timeframe` and authentic per-game lines from
  `players/gamelog` (shape re-verified 2026-07-07: Votto 2015 = 159
  entries, `game_date` YYYYMMDD), landed as raw envelopes under
  `data/cbs_raw/<league>/history/`. A gamelog is landed only when every
  entry dates inside the requested season — a dated entry from another
  year means fake history and the file is rejected, while null-date
  rows (postponed/cancelled games; 2021's COVID-makeup era has ~145)
  validate on their in-year `point` field instead. Idempotent per
  player-season; rerun to resume; player-seasons CBS's endpoint
  persistently 500s on are tombstoned as `KNOWN_UNAVAILABLE` with
  evidence (one so far: Votto 2006, a zero-MLB-games rostered
  prospect) so the verification verdict stays meaningful. First full
  sweep landed 2026-07-07: 3,809 player-season gamelogs across
  2004-2025 — 556,460 daily rows, 237,181 true player-games (the
  gamelog is a team-schedule-shaped daily log; `G` flags actual
  appearances) — with per-year universes fully covered and verdict
  PASS. Notably absent from per-game rows: FPTS — fantasy points per
  game must be recomputed from scoring rules at staging time, anchored
  against the authoritative season-grain FPTS in `league/stats`.
  Reuses the capture's GET-only whitelisted client (museum rule). The
  pitcher half followed 2026-07-09 (`--backfill-pitching`, see the
  Unreleased entry) once the `position=P` universe key was found.
- **CBS site-UI league-history capture** (`extract/cbs_ui_capture.py`,
  MLB-47): the site UI serves fantasy-layer history the API denies
  under every probed parameter — the maintainer found it browsing, and
  every mechanism turned out to be a clean GET. Lands raw HTML for
  standings 2001+ (champions were never formally named; final
  standings derive all 26 of them), transaction reports 2001+ under
  both filters (bench/start moves ride the log — in a pure points
  league the active set IS the scoring lineup, so active-points
  attribution back two decades becomes a measurable reconstruction),
  year-end roster reports 2003+ (the Time Period pulldown's option
  values are `/teams/roster-report/{team}/{year}/` URLs; per-year team
  ids parse from that year's standings page), drafts 2017+ (offline
  order, soft signal), and per-franchise `/history/team-overview/{id}`
  pages (franchise ids are stable across renames — the continuity
  join). Session cookie from `CBS_WEB_COOKIES` in the root `.env`,
  never logged; auth verified by content per page (login-bounce
  detection, per-surface markers); idempotent; ground-truthed against
  the maintainer's pasted 2021 roster before sweeping. First sweep
  landed 2026-07-08: 526 GETs, verdict PASS — 26 standings years, 52
  transaction pages (both filters; Activated/Reserved moves confirmed
  present in the 2001 log), 375 roster reports across 2003–2025 with
  per-year team counts that record the league's own shape (12–19
  teams by era), 10 drafts, 34 franchise overviews. The live season's
  roster-report pages render differently and are skipped — the API
  capture owns 2026. Era-specific parse notes (transaction verb
  vocabulary, draft page structure, embedded player-picker furniture)
  ride with the format-abstraction staging work.
- **Source freshness** on the four settings-style raw tables (seasonal
  thresholds over `extracted_at`); `box_scores` documented as pending an
  extract-side load timestamp.
- **Missing column docs**: `stat_classification.qualifier_stat` /
  `qualifier_min` seed columns, `mart_daily_roster_snapshot` grain
  columns.
- **A "Reading the DAG" section** in the dbt README explaining the three
  deliberate cross-layer edges (raw → team fact, the stat_classification
  hub, staging → roster snapshot) and the grain-wholesale rule of thumb
  they follow.
- **Shared stat-column doc blocks** (`marts/core/_stat_column_docs.md`):
  the 62 per-stat definitions the two wide active facts repeated
  verbatim now live once, referenced via `doc()`.
- **No-warehouse CI** (`.github/workflows/ci.yml`): unit suite + `dbt
  parse` against a placeholder profile on every push/PR; warehouse
  goldens stay local by design.
- **`mart_team_season_standings`** — season-grain standings contract:
  one row per (season_year, team_id) with the official W-L-T record,
  season sums of every scored-stat counting column, the calculated
  score lenses, points conceded, and the per-week normalization
  denominators (`scoring_days_played`, `standard_matchup_days`).
  Regular season only (`is_playoff = false`) — a standings freezes at
  the end of the regular season — while abnormal weeks stay in and are
  handled by the gameplay-day denominator. Lifted from the inline
  standings query in `output/almanac_data.py`, the same move that
  created `mart_team_matchup` in v1.1.1.
- **`mart_team_slot_production`** — season-grain lineup-slot production:
  one row per (season_year, team_id, lineup_slot) of calculated points
  produced while *deployed* in the slot (box-score slot, not position
  eligibility), joined to `dim_roster_slot_counts` for display order
  and the active / BE-IL cut. The mart keeps every deployed slot with
  an `is_active_lineup_slot` flag so the future bench/IL view reads the
  same contract; the v2.0 grid filters to active. Both new marts are
  tables (float sums feeding byte-diff goldens; same determinism
  rationale as `mart_team_matchup`), grain-tested, and declared on the
  `league_almanac` exposure.

- **Season-to-date report** (`output/generate_season_report.py`, MLB-1) —
  the milestone-summary entry point, built for the All-Star break post
  and extendable to the end-of-season edition. Deliberately
  calendar-agnostic: run any week, it reports through the latest loaded
  matchup period ("Through Week N"); occasion flavor comes from the note
  files below, not break-aware code. Sections mirror the weekly recap's
  BBCode idiom: best/worst team callouts on the per-gameplay-week lens
  (with top-3 season contributors on the bests), season Top
  Scorer/Hitter/Pitcher cards, Season Superlatives (best team and
  individual weeks, most points by a hero on the league_notes platform
  convention, biggest blowout, most points in a loss, fewest in a win —
  abnormal weeks excluded per the records convention — plus Game/Loss of
  the Week: the season's highest/lowest combined matchup totals and
  per-team weekly GotW/LotW appearance tallies), the season-to-date
  All-League Team (the almanac's optimal-lineup rows rendered as text
  with stat + slash lines), all-time records set or tied this season, and
  season Top Wasted Performances plus per-team wasted totals.
- **Optional summary header/footer note files**
  (`output/leagueNoteHeader.txt` / `leagueNoteFooter.txt`, gitignored;
  `output/note_files.py`) — printed verbatim as the first/last lines of
  every summary, weekly recap included; blank or missing files contribute
  nothing, so output is byte-identical until the commissioner writes one.
  LeagueNote.txt / Additional Notes keeps its locked behavior unchanged.

### Fixed

- **Quota retry during formula reapply turned into a hard 400.** gspread's
  `Worksheet.batch_update` rewrites each payload entry's `range` in place
  to `'<tab>'!<range>` before posting, so when a live write hit the Sheets
  per-minute quota mid-`_reapply_formula_cells`, the `_sheets_call` retry
  resent the already-prefixed list and the title doubled
  (`'HH'!'HH'!C7` → 400 "Unable to parse range"), killing the run a
  70-second wait should have saved. The reapply now hands gspread fresh
  dicts on every attempt; regression-tested with a fake worksheet that
  mimics the in-place mutation and a first-call 429. Latent since the
  v1.2 bref-links pass — it needed a quota hit to land exactly on a
  formula-reapply call (audited: the only values-API `batch_update` call
  site; the formatting-request batches don't get mutated).

Verification: dbt parse with zero deprecation warnings (the three
top-level test-arg blocks now use `arguments:`); dbt build green
(PASS=221 including the new marts' grain tests); all four singular
tests pass; recap / records goldens byte-identical. Almanac goldens
re-anchored for the Advanced Standings rework after a reviewed diff —
the only movement beyond that tab was the documented float-summation
residual (six 0.1-boundary point flips surfacing as ppg cells on four
team tabs, plus one Matchup History matchup whose Week-13 pitching
cell moved 148.0 → 148.1 from the latest-MP incremental re-merge —
the new values are the self-consistent ones).

## [1.2.0] — 2026-05-30

First product-feature release after the v1.1.x refactor line. Two surfaces:
the Home tab becomes a navigation-hub dashboard, and a net-new Draft Recap
tab adds the draft board + draft-value analysis (a new ESPN extract through
dbt to the Sheet). Reviewed tab-by-tab against the live Sheet; the almanac
byte-diff fixtures were re-baselined for both. No separate `Phase X.Y
Documentation.md` — the retrospective lives in `v1.x Handoff.md` "Status at
v1.2.0 ship," consistent with the v1.1.x releases.

### Added

- **Home two-band redesign.** Left navigation band (links to Records,
  Matchup History, the per-team pages, and Draft Recap; a points glossary;
  an all-time All-League Team) beside a right band with the All-League Team
  of the Week and Season-to-Date — each carrying two player-only "Total-Pts
  Best (incl. bench & FA)" deviation columns that surface where a bench / FA
  player out-produced the active pick at a slot. Nav links are live in-sheet
  `#gid` hyperlinks resolved at write time (a two-pass write: build the
  tabs, read their gids, render Home last), so they work on a brand-new
  sheet with no hardcoded URLs.
- **Draft Recap tab.** A new ESPN draft extract (`league.draft` →
  `RAW.DRAFT_PICKS`, folded into `--settings-only`) feeds `stg_draft` →
  `mart_draft_board`, joining every pick to its drafting team and the
  player's total season production. Side-by-side Best Value / Biggest Busts
  leaderboards (value = where a player was drafted vs. how they produced)
  over a round × team draft board with per-round Min / Median / Max and a
  production-keyed color scale. Keepers are flagged and ordered / valued by
  production (this is a keeper snake draft).
- **Owner display names** propagate through the mart (nickname > proper
  name), surfaced on the almanac Home, Records, and the recap.

### Changed

- **"Team Weeks" tab renamed to "Matchup History."**
- **Home All-League slash line** reads `.294/.390/.559`; the boxscore is a
  hyperlink on the Points cell; fantasy teams show their abbreviation.

### Fixed

- **Live-write formatting that the byte-diff never exercised.** The
  `--no-sheets` preview path skips the Sheets formatting code, so several
  bugs there were invisible: a `len(HOME_HEADER)` NameError on the
  new-Home-tab path; missing `almanac_write` imports that had silently
  skipped all per-team-tab and Matchup History conditional formatting since
  the v1.1.1 module split; and team-tab header rows mis-right-aligning the
  points glossary in column Q.

Verification: dbt build PASS=9 on the new draft models + tests; pytest
warehouse 16 passed (almanac byte-diff + recap / records goldens); pytest
default 144 passed (5 preexisting `test_almanac_sheets.py` failures
unrelated to this release). Open design calls (snake-draft presentation,
pick trading, a cross-session float-summation follow-up) are in
`v1.x Handoff.md`.

## [1.1.2] — 2026-05-27

Team-tab polish on the v1.1.1 almanac. No dbt changes and no
selection-logic changes — just rendering fixes and explanatory copy on
the per-team "Best Lineup" tabs, after QA'ing them in the live Sheet.

### Fixed

- **Points-per-game formats to two decimals.** `ppg` was rendering raw
  float precision (`3.232727…`, `1.0214`); now reads `#.##`.
- **Pitcher decision line shows W-L-Sv.** Was "decisions or saves,
  whichever is larger," which dropped W-L for closers and saves for
  swingmen who had both. Now `6-4` when there are no saves, `2-1-15`
  when the pitcher recorded saves.

### Added

- **Slot-fill explanation + points glossary on every team tab.** A short
  header note explains that the starting lineup is filled by Active
  Points at each eligible position and bench / IL / other by Total
  Points while rostered; a Total / Active / Inactive Points glossary
  sits over the all-time side; and a callout notes that points use
  current-season scoring (with an invite to request points as they were
  awarded at the time).

Verification: dbt unchanged; pytest warehouse green (16 passed,
including the regenerated almanac byte-diff); pytest default 144 passed
(5 preexisting `test_almanac_sheets.py` failures unrelated to this
release).

## [1.1.1] — 2026-05-27

Almanac refactor + optimal-team reframe. This release was scoped as a
refactor-only pass against the v1.1.0 golden TSV snapshot, and the first
half delivered exactly that: reusable analytical SQL moved into dbt
contracts, `output/almanac_sheets.py` split along data/logic/rendering
lines, output held byte-identical. The second half intentionally broke
byte-identity — a new optimal-team primitive reframes the All-League
Team and the per-team tabs from "who was actually slotted" to "the best
lineup the team could have fielded," scored on the calculated
(cross-season-normalized) points lens.

So this is the rare patch release that intentionally shifts product
output. The shift was reviewed tab-by-tab against the live Sheet before
the fixtures were re-baselined.

### Added

- **`get_optimal_team` primitive.** Parameterized "best lineup for any
  (timespan, scope, points_type)" dispatcher over the new
  `int_player_position_pts` model (per-position points via
  `LATERAL FLATTEN` of `eligible_slots`). Gap-based selection — fill the
  slot where the second-best option hurts most — with a disjoint-stat-
  categories rule so two-way players (Shohei) can fill one hitting and
  one pitching slot without double-counting.
- **`int_player_position_pts`** — per-position points accumulation at
  matchup grain, calculated-points lens.
- **`mart_team_matchup`** — wide matchup-grain view carrying opponent
  line, head-to-head margin, combined totals, and league-wide per-week
  averages. First consumer: the Team Weeks tab.
- **`fct_player_season_performance`** — slot-bearing season-grain rollup
  of the weekly player fact; foundation brick of the player-profile
  layer, carrying both calculated and platform point lenses.
- **Almanac byte-diff regression.** `tests/test_almanac_byte_diff.py`
  diffs generated TSVs against `tests/fixtures/almanac_v1_1_0/`,
  refreshable via `REGENERATE_BASELINES=1`.

### Changed

- **All-League Team + per-team Starters are now optimal-team selected.**
  The Home tab and every per-team tab fill their lineups by best-possible
  production at each position rather than by who was actually slotted
  there most often.
- **Per-team tabs reframed as "Best Lineup — current season + all-time."**
  Side-by-side current-season and franchise-history best lineups; Bench /
  IL / Other ranked by total rostered production (active + bench/IL
  points) to surface "could have helped but was blocked or benched"; an
  asterisk in the team column marks players still on the franchise.
- **Optimal teams scored on the calculated points lens** rather than
  platform points, so historical lineups answer "who would have done
  well under the league's current scoring" instead of "who scored well
  under whatever ESPN config was live at the time."
- **Rate-record qualifiers are seed-driven.** `mart_stat_leaderboard`
  rate-stat thresholds (`qualifier_stat` / `qualifier_min`) moved from
  hardcoded Python constants into `stat_classification.csv` / `dim_stat`.

### Fixed

- **Live-write crashes masked by the `--no-sheets` preview path.** Two
  separate breakages in `output/almanac_write.py` — missing
  `os`/`re`/`records` imports, and a dangling
  `get_all_league_team_season_to_date` call left behind by the
  dispatcher consolidation — surfaced only on the live Sheets write,
  which the byte-diff regression doesn't exercise. Both fixed.
- **Optimal-team rows render in canonical slot order.** The gap-based
  selector returns picks in fill order; it now sorts to baseball-card
  order (C, 1B, 2B, 3B, SS, IF, LF, CF, RF, OF, DH, UTIL, SP\*, RP\*)
  before returning, so hitters and pitchers no longer interleave on any
  tab.

### Internal

- **`output/almanac_sheets.py` split** into `almanac_data` (SQL + data
  shaping), `almanac_logic` (selection + tab-row building),
  `almanac_render` (formatters), and `almanac_write` (Sheets API).
  `almanac_sheets.py` is now a thin facade re-exporting every public
  name, so existing import sites keep working unchanged.
- **New dbt contracts route the almanac through the mart layer** instead
  of reaching past it into intermediates.

Verification: dbt build clean (PASS=161 / WARN=0 / ERROR=0 / NO-OP=4);
pytest warehouse green (16 passed, including the almanac byte-diff and
the records + recap BBCode goldens); pytest default 144 passed (5
preexisting `test_almanac_sheets.py` failures unrelated to this release).
Per-team and Home tabs visually QA'd against the live Sheet.

## [1.1.0] — 2026-05-22

League almanac release. This is the first v1.x product expansion after
the stable BBCode/records foundation: a browsable Google Sheets workbook
for league members, with a Home tab, curated Records tab, Team Weeks
archive, and one active-stats tab per fantasy team.

This ships intentionally before the almanac internals are fully refactored.
The output is product-ready enough to collect league feedback; the known
architectural debt is that several almanac analytical queries still live
inside `output/almanac_sheets.py` instead of dbt contracts. v1.1.1 is
reserved for a refactor-only pass against the golden TSV snapshot captured
in this release.

### Added

- **League almanac Google Sheets surface.** New
  `output/generate_almanac_sheet.py` entry point and
  `output/almanac_sheets.py` writer build a multi-tab workbook:
  `Home`, `Records`, `Team Weeks`, and one team active-stats tab per
  fantasy team.
- **Home tab.** Surfaces an all-league team of the week and
  season-to-date all-league lineup, filled from the league's configured
  roster shape instead of hardcoded lineup assumptions.
- **Curated Records tab.** Side-by-side current-season and all-time
  record book covering score records, team hitting/pitching records,
  rate records, and lineup-slot records. Period cells link to ESPN
  matchup-view boxscores; rate records use the documented 225 AB /
  50 IP thresholds at output time.
- **Team Active Stats tabs.** Each team gets a browsable current-season
  and all-time roster-history view: likely starting lineup by active
  usage, bench/IL/other sections, current fantasy-team abbreviations,
  rostered days, games, active points, bench/IL points, PPG, and compact
  hitter/pitcher stat lines.
- **Team Weeks tab.** Wide team-week archive with scored hitting and
  pitching stats, calculated hitting/pitching/total points, margin,
  matchup totals, league averages, matchup links, color scales, hidden
  helper columns, and record emphasis for standard-length weeks.
- **Roster-settings extraction.** `extract/extract.py` now persists the
  ESPN `rosterSettings` payload into `raw.roster_settings`.
- **Roster-setting marts.** New `dim_roster_slot_counts` exposes lineup
  slot counts and position maximums; new `mart_daily_roster_snapshot`
  gives roster-history consumers a shell that includes zero-stat
  rostered players.
- **Golden almanac snapshot.** `tests/fixtures/almanac_v1_1_0/` captures
  the v1.1.0 TSV output as the byte-diff baseline for the planned
  v1.1.1 refactor.

### Changed

- **Boxscore links now target matchup view.** Almanac links use ESPN's
  `view=matchup` URL shape so they open the full matchup view rather
  than the final scoring day.
- **Generated preview artifacts are ignored.** `.gitignore` now excludes
  almanac preview directories and ad-hoc TSV reports under `output/`.
- **dbt catalog metadata.** Exposures and overview docs now declare the
  almanac as a first-class downstream consumer alongside the recap and
  records report.

### Internal

- **Almanac unit coverage.** `tests/test_almanac_sheets.py` covers the
  roster-fill logic, record-tab shaping, team-week shaping, and formatting
  helpers that are most likely to drift during the v1.1.1 refactor.
- **Known v1.1.1 refactor target.** Move reusable analytical SQL out of
  `output/almanac_sheets.py` into dbt contracts where appropriate
  (`mart_team_matchup`, player/team/slot history), split data/logic/
  rendering modules, and keep the generated TSV snapshot byte-identical.

Verification: dbt build clean (PASS=140 / WARN=0 / ERROR=0 / NO-OP=4,
including 120 data tests and 4 exposures); dbt static docs regenerated;
pytest default green (148 passed, 15 deselected); pytest warehouse green
(15 passed, 148 deselected).

## [1.0.2] — 2026-05-19

DAG hygiene + dbt-architecture cleanup release. No consumer-visible
behavior change — the recap and records report render byte-identical
output pre- vs. post-refactor against the golden BBCode regression.
What changed is internal: the dbt DAG got a contract-layer cleanup
that separates "config seeds" from "data marts," promotes a real
weekly fact out of the intermediate layer, and adds a daily fact that
gives output scripts a mart-layer entry point instead of reaching
back into intermediates.

Strictly per semver this is arguably a 1.1.0 (new public models
shipped). Per the maintainer's reading, v1.x stays reserved for the
`dim_player` flagship inflection where consumer-visible behavior will
shift; 1.0.x stays "polish + refactor" releases. Treat 1.0.2 as
"the v1.0 architecture, redrawn."

### Added

- **`dim_stat`** — mart-layer dimension over the `stat_classification`
  seed. Adds a `leaderboard_name` column (seed-name post translation:
  `1B` → `SINGLES`, `30` → `CYC`, `64` → `SHO`, etc.) and carries all
  other seed columns through unchanged. Single source of truth for the
  seed → leaderboard name translation; `output/stat_catalog.py` and
  the `mart_stat_leaderboard` compile-time loop both read from here.
- **`dim_matchup_period`** — mart-layer dimension over the
  `matchup_schedule` seed. Carries calendar metadata
  (`is_abnormal` / `is_playoff` / `playoff_round` / start/end dates)
  for consumer-side reads.
- **`fct_player_daily_performance`** — mart-layer fact over
  `int_player_daily`. Exposes per-day data (counting stats, point
  contributions, per-day platform totals, per-day metadata) to
  consumers via a contract layer. Adds `performance_status` and
  `wasted_bucket` columns derived centrally from `lineup_slot` and
  inherited up through the weekly facts.
- **Schedule columns on the four weekly facts.** `is_abnormal`,
  `is_playoff`, and `playoff_round` denormalized from
  `dim_matchup_period` onto `fct_weekly_player_active_performance`,
  `fct_weekly_player_inactive_performance`,
  `fct_weekly_team_active_performance`, and
  `fct_weekly_team_inactive_performance`. Consumers can filter abnormal
  weeks and render week labels directly off fact rows without a
  schedule-lookup dict.

### Changed

- **`int_player_weekly_performance` promoted to
  `fct_weekly_player_performance`.** Renamed and moved from
  `dbt_league/models/intermediate/` to `dbt_league/models/marts/`.
  Re-sourced from `fct_player_daily_performance` so the
  daily-to-weekly DAG edge is load-bearing rather than a side branch
  off the int layer. Materialized as a table (was a view) since two
  downstream facts (active, inactive) read from it on every
  `mart_stat_leaderboard` query.
- **Consumer migration.** `output/generate_summary.py::get_wasted_
  points` and several `output/league_notes.py` callouts repointed from
  `int_player_daily` to `fct_player_daily_performance`.
  `output/records_data.py::load_schedule_lookup` repointed from
  `matchup_schedule` to `dim_matchup_period`. `output/stat_catalog.py`
  repointed from `stat_classification` to `dim_stat`, and helpers
  consolidated to read `leaderboard_name` directly from the dim
  (rather than re-applying the Python-side `SEED_TO_LEADERBOARD`
  mapping).
- **`mart_stat_leaderboard`:**
  - Four `INNER JOIN matchup_schedule ... WHERE s.is_abnormal = false`
    patterns (one per source CTE) simplified to
    `WHERE f.is_abnormal = false` against the denormalized column on
    each fact.
  - Compile-time `run_query` switched from `ref('stat_classification')`
    to `ref('dim_stat')`; duplicate CASE block deleted. The seed →
    leaderboard name translation now lives in exactly one place
    (`dim_stat.sql`).
  - `current_year` CTE switched from `source('raw', 'box_scores')` to
    `ref('fct_weekly_team_active_performance')`. One fewer raw-source
    edge in the catalog DAG.
- **`mart_league_weekly_benchmarks`:** dropped the
  `matchup_schedule` JOIN that existed purely for the `is_abnormal`
  filter; now uses the denormalized column on the weekly team fact.
- **`output/records_data.py::league_history_count`:** dropped the
  `matchup_schedule` JOIN for the same reason.
- **dbt exposures** (`dbt_league/models/exposures.yml`) routed through
  the new mart-layer contracts (`dim_stat`, `dim_matchup_period`,
  `fct_player_daily_performance`) instead of the old `int_player_daily`
  / `matchup_schedule` / `stat_classification` direct references.
  `weekly_recap` gains `mart_league_weekly_benchmarks` (added at v1.0.1
  but never declared on the exposure).
- **`tests/capture_row_counts.py`:** `MODELS` list updated for the
  rename and additions.

### Removed

- **`SEED_TO_LEADERBOARD` constant + `to_leaderboard_name` function**
  from `output/stat_catalog.py`. Replaced by `dim_stat.leaderboard_
  name`. Three corresponding tests in `TestToLeaderboardName` deleted;
  translation coverage retained transitively via
  `TestDisplayMap::test_translation_applied`.

### Internal

- `BRAINTHOUGHTS.md` private working-notes doc convention: four-section
  structure (Wishlist / Clarifications / Discussions and Tweaks /
  Interview Questions). Reviewed at every push; entries never deleted
  except under a narrow "CLARIFICATIONS doesn't need to preserve
  example-bound details about replaced architecture unless the
  framing is a teaching moment" carve-out.

Verification: dbt build clean (116 PASS / 0 ERROR with the new models
and tests); pytest tests/ green (113 passed); pytest tests/ -m
warehouse green (15 passed including byte-diff golden BBCode
regression — confirms the refactor is consumer-side transparent).

## [1.0.1] — 2026-05-18

A v1.0 polish release. Strictly speaking the changes here include
several new features that would justify a v1.1.0 under a strict
semver reading — record-surfacing for NEGATIVE_POINTS, the Hit-for-
the-Cycle stat, a league-wide benchmarks mart, an always-on "League
This Week" recap line, eight new league_notes callouts, and key-pair
Snowflake auth. The maintainer chose to land them as a 1.0.x patch
to keep the v1.x label reserved for a more meaningful structural
inflection (the player-entity flagship). Treat 1.0.1 as "polish that
grew."

### Added

- **New tracked records.** `NEGATIVE_POINTS` (gross-negative-production
  rollup, already on the four facts) and `CYC` (Hit for the Cycle, new
  wide column propagated through `int_player_daily`, `int_player_weekly_
  performance`, and all four facts) promoted to `is_record_candidate=
  true` and surfaced in the records report.
- **`League This Week:` always-on summary line.** First line of the
  weekly recap, surfaces the league's mean overall / hitting / pitching
  points alongside the historical ranking ("273.4 (2nd of 30) points
  overall …"). Renders every week regardless of whether anything
  noteworthy fired; foregrounds league-level context as a baseline.
- **Eight new league_notes callouts:**
  - `cycles` — per-player cycle announcement with cumulative history
    ordinal and "first of the season" flourish.
  - `no_quality_starts` — teams that started at least one SP but
    produced zero QS; cumulative 0-QS-with-starts ordinal.
  - `hr_streak_active` — teams whose ≥7-day HR streak is still alive
    at MP end; cites all-time league record for context.
  - `hr_streak_ended` — streaks of ≥10 consecutive HR-days that broke
    in the recap MP.
  - `hero` — second-banana-margin walk-off lens: a player whose
    individual outperformance vs. their #2 single-handedly closed the
    margin in a narrow win.
  - `scapegoat` — symmetric loss-attribution lens: a player whose
    negative output exceeded the loss margin.
  - `mismatch` — top vs. bottom scorer in the same head-to-head
    matchup; cumulative-margin-rank ordinal.
  - `no_negative_days` — teams where every active player-day had
    `platform_points ≥ 0`; "first of the season" flourish on first
    qualifying team.
  - `hot_week` / `cold_week` — league-level outlier callouts driven
    by the new benchmarks mart.
- **`mart_league_weekly_benchmarks`** — aggregate of league-week means
  + percentile rank within league history for overall / hitting /
  pitching points. Powers the always-on `League This Week:` line and
  the hot/cold-week callouts. Future surfaces (frontend, dashboard)
  read from one mart instead of recomputing.
- **`output/league_notes.py` registry pattern** is now the single home
  for all conditional flavor callouts (matchup-outcome lenses migrated
  in from inline `generate_summary.py` definitions). `render_callouts`
  inserts a blank-line separator between callouts that fire, preserving
  the prior inline rendering.
- **Snowflake key-pair authentication** support in `output/db.py` and
  `extract/extract.py`. Required after MFA enforcement on the account;
  password-based auth fails with an interactive MFA prompt the
  connector can't satisfy. `SNOWFLAKE_PRIVATE_KEY_PATH` + optional
  `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` env vars; full walkthrough in
  SETUP.md §4.
- **Recap polish:** "None recorded yet (across N team-weeks)" rendering
  for records with floor-zero values (no-hitters, perfect games,
  cycles) instead of the ambiguous "0 across N team-weeks" form;
  conditional Top Scorer line that suppresses when the overall winner
  duplicates Top Hitter or Top Pitcher.
- **Random seed determinism.** `random` seeded per-recap so varied-
  template callouts (e.g., `hr_drought`) pick the same phrasing across
  rebuilds of the same MP.
- **dbt docs catalog hosted via GitHub Pages** — initial publish at
  https://kyledawson24.github.io/fantasy-league-front-page/.

### Changed

- **`is_always_tracked` seed column → `auto_tracked`** with corresponding
  rename of `stat_catalog.get_always_tracked()` →
  `get_auto_tracked()` and the `always_tracked` parameter on
  `records_logic.should_track_record` → `auto_tracked`. The new name
  separates "tracked regardless of league scoring settings" cleanly
  from the implicit "tracked because the stat is scored" pathway.
- **Mislabeled `stat_name='CYC'` seed row** (ESPN stat ID 31, a
  non-cycle daily-achievement flag) renamed to `STAT_31` so the real
  cycle stat (id 30) can own the `CYC` leaderboard column.
  `stg_player_stat_breakdowns` now filters wrapper-emitted `'CYC'`
  rows so the seed FK invariant holds without the mislabel row.
- **Score totals rounded at the fact layer.** `calculated_*`,
  `platform_*_pts`, `platform_points`, and `negative_points` rounded
  to 1 decimal at the player-fact layer; team-fact totals inherit
  exactness from `SUM(NUMBER)` arithmetic so the team_total =
  SUM(players) invariant holds. Kills the cosmetic 126.9 ↔ 127.0
  wobble seen across `--full-refresh` rebuilds.
- **`records_logic` import-pure** with respect to the data layer. The
  only logic→data import (`count_value_occurrences`) replaced with a
  `count_fn` parameter injected by `records.get_records_with_
  contributors`. The saturated-tier branch is now unit-testable
  without a Snowflake round-trip; four new tests cover it.

### Fixed

- **`no_quality_starts` historical ordinal drift.** The cumulative
  count used `league_history_count('team', 'QS', 0)`, which includes
  team-MPs where no SP started at all — so the ordinal drifted upward
  from the trigger's actual definition. Fix mirrors the trigger's
  `lineup_slot='SP' AND games_played≥1` filter in the historical
  count.
- **`hr_streak_active` "new record" claim on tied streaks.** Said "a
  new league record" when the longest active streak matched the
  existing record. Two issues — (a) `record_len` included the active
  streak itself, and (b) the comparison was `>=` rather than `>`.
  Fix: exclude the current longest active run from the prior-record
  calculation and split into explicit new / tied / existing-stands
  branches.
- **Hero template trailing whitespace.** Two templates ended with a
  space inside the string (rendered into baselines); two more had
  trailing whitespace after the closing quote. Cleaned up.

### Removed

- Parked `_replace_tab` formatting-preservation change in
  `output/sheets_writer.py` discarded — pending Sheets surface
  redesign supersedes the in-place-update logic.

## [1.0.0] — 2026-05-13

First stable release. Phase 7 was a portfolio-prep rearchitect spanning
an 8-step dbt overhaul (Steps A–H), a three-way split of `records.py`,
repo hygiene, and public documentation. The active dbt DAG was reduced
from 9 to 7 business-logic models with a symmetric active/inactive
split at both grains, stat metadata moved from scattered Python
dictionaries into a seed-driven catalog, and the project gained tests
plus release tooling.

### Added
- Seed-driven `stat_classification.csv` (97 rows, 13 columns) as single
  source of truth for stat metadata: display names, abbreviations,
  polarity, record-candidate flags, derivation expressions. Adding a
  tracked stat is now one CSV row, not edits in five Python locations.
- `output/stat_catalog.py` — six `lru_cached` accessors over the seed
  (`get_display_map`, `get_abbrev_map`, `get_polarity_map`,
  `get_always_tracked`, `get_record_candidates`, `get_derived_exprs`).
- `output/records_data.py` + `output/records_logic.py` — `records.py`
  split into Snowflake-querying layer + pure consumer-side rules layer
  (was 930 lines / 22 functions; now three files with backward-compat
  re-exports so consumer scripts and tests didn't have to change).
- `output/db.py` — consolidated Snowflake connection wrapper.
- 112 pure pytest + 15 warehouse-marked tests, including byte-diff
  golden-output regression against pinned BBCode baselines for both the
  weekly recap and the all-time records report.
- `tools/regen_stat_classification.py` — idempotent seed-regen tool.
- MIT LICENSE, this CHANGELOG, README, SETUP, ROADMAP, and dbt docs
  (hosted via GitHub Pages).

### Changed
- Active facts renamed to `fct_weekly_{team,player}_active_performance`;
  new symmetric inactive facts `fct_weekly_{team,player}_inactive_performance`.
  Active = fantasy reality (what the manager actually played); inactive =
  MLB reality (what the player did regardless of fantasy rostering).
- `mart_stat_leaderboard` rebuilt via seed-driven Jinja UNPIVOT over all
  four facts; previously a hand-maintained UNION block in SQL.
- `performance_status` partition column added to the mart so consumers
  can filter active-vs-inactive symmetrically.
- requirements.txt converted UTF-16 LE → UTF-8 LF, re-sorted
  alphabetically, `pytest>=7.0` added.

### Removed
- Six dbt models retired during the rearchitect; the active DAG is now
  7 business-logic models (down from 9). Full list and per-model
  rationale in `Phase 7 Documentation.md`.
- Scattered Python polarity / always-tracked dicts in `records.py`;
  values now live in the seed.

See `Phase 7 Documentation.md` for architectural detail.

## [0.6.0] — 2026-05-08

### Added
- Google Sheets sink as an opt-in second consumer surface for the
  records report. Three-tab layout: rank-1 records, top-5 with
  contributors, full leaderboard dump.
- `output/records.py` — consolidated SQL and polarity-filter logic in
  one place so both consumer scripts (recap + records report) share a
  single data-access layer.
- Tracked-stats expansion to surface derived counters (PA, SB-CS, W-L,
  SV-BLSV) and rate stats (ERA, WHIP, K/9, K/BB, HR/9, BB/9) at team grain.
- Tie-collapse rule: when more entities tie than the visible top-N,
  collapse into a single "N tied at X" row, with inline holders preserved
  for small tiers (≤3 members).
- Bulk contributor fetches: one batched SELECT per grain rather than N
  round-trips.

### Changed
- Mart record-direction values renamed: `best`/`worst` → `most`/`fewest`.
- Records section displays owner names; recap section doesn't —
  separation of audience (recap is fast-glance; records reward attribution).

See `Phase 6.3.3 Documentation.md`.

## [0.5.0] — 2026-05-04

### Added
- "Records set this week" callouts in the recap, surfacing new or tied
  records broken during the just-played matchup.
- Polarity-aware record filtering: negative-stat "fewest" records
  suppressed where they'd trivially be zero; "always-tracked" override
  for stats that should surface regardless of polarity.
- Recap restructured around the player-card → team-card → records
  narrative.

### Changed
- League records keyed off `calculated_*` columns (project-owned,
  scoring-weight-derived) rather than `platform_*` (ESPN-reported,
  drift-prone across rule changes). Platform values retained for audit.
- Wasted-points scope extended to active players who scored negative
  points — the manager could have benched them rather than letting the
  negative drag the lineup. Generalizes "wasted" as points achievable
  via free lineup changes (start a bench player, sign a free agent,
  sit a negative scorer) that weren't made.

See `Phase 5.0 Documentation.md`.

## [0.4.0] — 2026-05-02

### Added
- Wasted-points concept introduced: per-player-per-matchup tracking
  of points the manager could have captured but didn't — initially
  scoped to bench-side waste (productive players left on the bench
  while their owner started someone else).
- Slot validity model distinguishing roster slot (e.g., "OF") from
  player eligibility (e.g., "OF, 1B") to handle Ohtani-class two-way
  and multi-eligible players correctly.
- Kona anti-join pattern: point-in-time roster status reconstruction
  for free-agent tracking without requiring a transaction log.

### Changed
- Player roster surface migrated onto the `kona` wrapper for richer
  per-player game-level data.

See `Phase 4.0 Documentation.md`.

## [0.3.4] — 2026-05-01

### Changed
- Raw-always extraction: simplified the doubleheader-fix code path from
  hybrid (capture extra splits only on detected DH days) to unconditional
  per-day-per-platform-level raw capture. Same downstream output, cleaner
  mental model.

## [0.3.3] — 2026-04-30

### Fixed
- Silent doubleheader stat-overwrite bug in `espn-api`'s `box_scores()`
  wrapper: the wrapper built a dict keyed by `scoringPeriodId` and
  silently dropped one game when ESPN returned multiple splits for the
  same period. Root-caused via raw-API inspection (preserved in
  `archive/phase_3.3_doubleheader_debug__turang_raw.json`); fix is to
  capture ESPN's pre-aggregation rows directly and aggregate ourselves.

See `Phase 3.3 Documentation.md`.

## [0.3.2] — 2026-04-29

### Added
- `calculated_points` columns derived from scoring-settings seed × stat
  counts, computed in dbt so the project owns the authoritative number
  rather than relying on `platform_points` reported by ESPN.
- `stg_scoring_settings` staging model parsing ESPN's settings JSON.

### Changed
- Output scripts surface `calculated_*` as the canonical "points" number;
  `platform_*` retained for ESPN-side audit comparison.

See `Phase 3.2 Documentation.md`.

## [0.3.1] — 2026-04-26

### Added
- "Wide convergence" facts: `fct_weekly_player_stats` and
  `fct_weekly_team_stats` combine counting stats with derived rates in
  one fact per grain. Resolves the prior counting-vs-rate cross-mart
  dependency flagged in Phase 2.0.

See `Phase 3.1 Documentation.md`.

## [0.3.0] — 2026-04-24

### Added
- Stat-level league records: most HRs, most Ks, most SBs, etc., with
  top-10 leaderboards scoped to all-time and current season.
- `mart_stat_leaderboard` view.
- Rate-stat macro library: AVG, OBP, SLG, OPS, ERA, WHIP, K/9, K/BB
  defined once, applied at any grain the analyst needs.
- Incremental dbt models for the weekly stat facts (composite
  `(season_year * 100 + matchup_period)` scalar; reprocesses the latest
  period to handle late-arriving stat corrections).

See `Phase 3.0 Documentation.md`.

## [0.2.1] — 2026-04-22

### Added
- Owner names end-to-end: player → owner mapping wired through staging,
  marts, and output script (recap + records sections).
- `owner_nicknames.csv` seed for preferred display names.

### Changed
- Mart consolidation: merged two-mart team-scores structure to resolve
  the cross-layer dependency flagged in 2.0.
- Player points model reworked at weekly grain to handle two-way
  players (Ohtani) correctly.

See `Phase 2.1 Documentation.md`.

## [0.2.0] — 2026-04-20

### Added
- Player-level contribution callouts in the weekly summary: top
  contributors per Best/Worst Hitting/Pitching team.
- Records section: current-season + all-time team records for
  Best/Worst Matchup Total, Hitting, Pitching.
- Footnotes for abnormal-week exclusions and scoring rule changes.

See `Phase 2.0 Documentation.md`.

## [0.1.0] — 2026-04-19

### Added
- Initial end-to-end pipeline: ESPN Fantasy API → Python extractor →
  Snowflake raw JSON → dbt staging/intermediate/mart → Python BBCode
  summary generator.
- Weekly summary: Best/Worst Overall, Hitting, Pitching with the
  conditional Tough Luck, Lucky Bastard, and Fair-and-Just-League
  callouts.
- dbt project scaffold: staging (`stg_box_scores`), intermediate
  (`int_team_daily_scores`, `int_weekly_matchups`), mart
  (`fct_weekly_team_scores`).

See `Phase 1.0 Documentation.md`.
