-- stg_player_stat_breakdowns.sql
-- Flatten the breakdown VARIANT from stg_box_scores into one row per
-- (season_year, scoring_period, team_id, player_id, stat_name).
-- Mechanical reshape only -- business filters (active slots, counting stats)
-- are applied in intermediate.
--
-- Phase 7 B1: filter out raw rate-stat keys K/9 and K/BB. ESPN emits these
-- per-pitcher in the breakdown VARIANT but they're not aggregatable for our
-- team-level pipeline (we compute K_PER_9 / K_PER_BB from K and OUTS at the
-- mart). The seed got the K/9 / K/BB rows repurposed (renamed to K_PER_9 /
-- K_PER_BB) so the FK test would otherwise fail on these stg rows; the
-- filter keeps the FK invariant clean. Rows are dropped at int anyway via
-- is_counting=false, so no downstream consumer notices.

with players as (
    select * from {{ ref('stg_box_scores') }}
),

flattened as (
    select
        season_year,
        scoring_period,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        player_id,
        player_name,
        position,
        lineup_slot,
        lineup_slot_category,
        b.key::string   as stat_name,
        b.value::float  as stat_value
    from players,
        lateral flatten(input => breakdown) b
    where breakdown is not null
      and b.key::string not in ('K/9', 'K/BB')
)

select * from flattened
