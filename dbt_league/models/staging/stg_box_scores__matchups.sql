-- stg_box_scores__matchups.sql
-- The matchup grain of the raw box-score payload: one row per matchup per
-- scoring period, carrying the team-level scalars already typed and each
-- side's lineup as a sub-document for stg_box_scores to flatten.
--
-- WHY THIS IS ITS OWN MODEL (MLB-10). It was a CTE inside stg_box_scores
-- until the port, and splitting it is a MEMORY boundary, not a modelling
-- opinion -- the values and the grain of stg_box_scores are unchanged.
--
-- DuckDB's `cast(json as json[])` materializes an array where Snowflake's
-- LATERAL FLATTEN streams one, and an extracted element keeps its parent
-- document alive. stg_box_scores nests two of those flattens -- matchups,
-- then each matchup's lineup -- so every one of the 141,350 player rows
-- was transitively pinning a 236 KB raw payload. Writing the result then
-- wanted 8-10 GB against a 6 GB cap: about 47 KB per output row, roughly
-- 70x the text those rows actually contain.
--
-- Materializing here breaks the chain. The parent payload is dropped at
-- this table boundary, so the lineup flatten downstream reads a narrow
-- table instead of a retained JSON document. Measured on the full corpus
-- at the 6 GB cap with 4 threads: 1.5s here + 1.2s downstream, against an
-- OOM for the single-model form. (Single-threading the old shape also fit,
-- at 33s, but throttling the whole build to hide one model's shape is a
-- worse answer than fixing the shape.)
--
-- The 6 GB cap is deliberately not what moves: it is the "runs on a
-- stranger's laptop" promise (MLB-109/127), so raising it would delete the
-- acceptance criterion rather than meet it.
--
-- This is the house pattern for the same class elsewhere, int_player_daily
-- included: PROJECT the sub-document you need before flattening it, and
-- put a materialization boundary between nested flattens so no row carries
-- a parent it has finished with.
--
-- Snowflake is indifferent to the split -- FLATTEN streams either way --
-- so this costs one extra small relation there and changes no value.
--
-- Grain: one row per (league_key, season_year, scoring_period, matchup).

{{ config(materialized='table') }}

with matchup_source as (
    -- Phase 4 raw shape: {"matchups": [...], "free_agents": [...]}.
    -- Pre-Phase-4 raw shape: a bare array of matchup dicts. raw_json:matchups
    -- is NULL on the array shape, so the COALESCE falls through to raw_json
    -- itself and the legacy rows still flatten. Both arms stay ARRAYS, which
    -- DuckDB requires -- a JSON object there raises rather than yielding zero
    -- rows, where Snowflake's flatten would quietly return nothing.
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        coalesce({{ json_get('raw_json', 'matchups') }}, raw_json) as matchups_json
    from {{ source('raw', 'box_scores') }}
),

matchups as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        m.value as matchup
    from matchup_source,
        {{ flatten_array('matchups_json', 'm') }}
)

select
    league_key,
    season_year,
    scoring_period,
    matchup_period,
    {{ json_text('matchup', 'home_owner') }}::string        as home_owner,
    {{ json_text('matchup', 'home_team') }}::string         as home_team,
    {{ json_text('matchup', 'home_team_id') }}::integer     as home_team_id,
    {{ json_text('matchup', 'home_team_abbrev') }}::string  as home_team_abbrev,
    {{ json_text('matchup', 'away_owner') }}::string        as away_owner,
    {{ json_text('matchup', 'away_team') }}::string         as away_team,
    {{ json_text('matchup', 'away_team_id') }}::integer     as away_team_id,
    {{ json_text('matchup', 'away_team_abbrev') }}::string  as away_team_abbrev,
    {{ json_get('matchup', 'home_lineup') }}                as home_lineup,
    {{ json_get('matchup', 'away_lineup') }}                as away_lineup
from matchups
