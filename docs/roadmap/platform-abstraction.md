# Platform Abstraction

**Linear:** [Platform Abstraction](https://linear.app/roguelitedevelopment/project/platform-abstraction-b10d9137338b) · Planned · High

The hidden prerequisite under every multi-platform and self-serve idea — and
the place the dbt investment earns its keep: sources with nothing in common
but the sport, converging at the staging boundary into identical readable
outputs.

Four workstreams (Linear milestones):

1. **Adapter contract v1** — what any platform extract must deliver
   (box-score grain, matchup pairs + scores, settings, rosters); ESPN
   refactored to be adapter #1, behavior-neutral.
2. **Stat vocabulary crosswalk** — `stat_classification` is ESPN-stat-ID
   keyed today; introduce a canonical stat key + per-platform mapping seeds
   (the `dim_stat.leaderboard_name` pattern, one level up).
3. **Schedule derivation** — retire the hand-tagged `matchup_schedule` seed.
   Prior scoping (from the pre-Linear roadmap): derive from
   `settings.matchup_periods` + `regular_season_count` +
   `playoff_team_count`; append-only `raw.matchup_schedule`; a tiny
   `matchup_schedule_overrides.csv` carrying only `is_abnormal` patches.
   Maintainer has additional heuristics in mind — confirm scope before code.
4. **Settings ingestion** — scoring weights + roster shape per platform.

Two-way (Ohtani-class) semantics get explicit attention: the slot-validity
filter, deployed-slot crediting, and the platform hitting/pitching split all
carry ESPN assumptions. Stretch goal parked here: cross-platform league
stitching (an ESPN league that migrated to Yahoo reads as one franchise
history — needs a player-ID crosswalk and franchise mapping).

**Depends on:** nothing — it is the foundation for Multi-Platform Support and
the Self-Serve Web App.

**Seeded issues:** MLB-3 adapter contract · MLB-4 stat crosswalk · MLB-5
schedule derivation · MLB-6 settings ingestion · MLB-7 two-way semantics ·
MLB-8 league stitching (stretch)
