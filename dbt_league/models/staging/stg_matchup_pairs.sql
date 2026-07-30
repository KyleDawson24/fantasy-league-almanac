-- stg_matchup_pairs.sql
-- One row per (league_key, season_year, matchup_period, home_team_id, away_team_id):
-- the weekly matchup spine -- who played whom, with home/away sides --
-- extracted from the raw box-score JSON.
--
-- Pairs are collected across EVERY scoring period of the matchup (first
-- appearance wins via the QUALIFY) rather than from the last scoring
-- period only, so a capture glitch on any single day cannot drop a
-- pairing. COALESCE handles both legacy raw shape (bare matchups array)
-- and the Phase-4 shape (dict with a matchups key).

{{ config(materialized='view') }}

select distinct
    league_key,
    season_year,
    matchup_period,
    m.value:home_team_id::integer as home_team_id,
    m.value:away_team_id::integer as away_team_id
from {{ source('raw', 'box_scores') }},
    {{ flatten_array('coalesce(raw_json:matchups, raw_json)', 'm') }}
qualify row_number() over (
    partition by league_key,
        season_year,
        matchup_period,
        m.value:home_team_id::integer,
        m.value:away_team_id::integer
    order by scoring_period
) = 1
