-- stg_player_stat_breakdowns.sql
-- Flatten the breakdown VARIANT from stg_box_scores into one row per
-- (season_year, scoring_period, team_id, player_id, stat_name).
-- Mechanical reshape only -- business filters (active slots, counting stats)
-- are applied in intermediate.
--
-- Filter out wrapper-emitted breakdown keys that don't correspond to a
-- seed row we want to carry:
--   - 'K/9' and 'K/BB' (Phase 7 B1): per-pitcher rate stats emitted by ESPN
--     but not aggregatable for our team-level pipeline. The seed repurposed
--     the rows as K_PER_9 / K_PER_BB so these keys would otherwise fail
--     the FK test. Dropped at int anyway via is_counting=false.
--   - 'CYC' (v1.x): the wrapper translates ESPN stat ID 31 to 'CYC' but
--     that data is NOT cycles (148 non-zero rows across 113 players -- see
--     STAT_31 seed row notes). v1.x renamed the seed row to STAT_31 so the
--     real cycles stat (id 30) can own the CYC leaderboard column without
--     a dict-collision in stat_catalog. Filtering wrapper 'CYC' rows here
--     preserves the FK invariant without resurrecting the mislabel.

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
      and b.key::string not in ('K/9', 'K/BB', 'CYC')
)

select * from flattened
