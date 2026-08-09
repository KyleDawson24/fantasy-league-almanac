-- dim_team_season_standing.sql
-- Consumer-facing dimension for how each team finished each season, as the
-- PLATFORM reports it (MLB-227). Joins the flattened standings to their
-- division so a consumer gets the label without re-deriving it.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id).
-- ==========================================================================
--
-- THIS IS THE PLATFORM'S ANSWER, NOT A RECONSTRUCTION, and that is the whole
-- reason it exists. ESPN seeds division winners ahead of the field, so no
-- ordering over wins/points reproduces its seeding -- see
-- stg_team_standings for the 2026 counterexample. Consumers that need "who
-- was the N seed" should read playoff_seed here rather than sorting a
-- standings query.
--
-- made_playoffs is derived by comparing the seed to the season's own
-- playoff_team_count rather than hardcoding 8, because the league's shape
-- changes between seasons (16 teams / 4 divisions in 2025, 14 / 2 in 2026).
-- It is NULL, not false, when the seed is unassigned -- an in-progress
-- season has no answer yet and should not be rendered as "missed".
--
-- Division columns are LEFT joined: a season with no divisions configured is
-- a legitimate league shape, and a team whose division_id has no matching
-- division row should surface as a NULL label rather than vanish from the
-- dimension. The schema test on division_id is what makes the second case
-- loud instead of silent.

{{ config(materialized='view') }}

select
    s.league_key,
    s.season_year,
    s.team_id,
    s.team_abbrev,
    s.team_name,

    s.division_id,
    d.division_name,
    d.division_size,

    s.playoff_seed,
    s.final_rank,
    sc.playoff_team_count,
    case
        when s.playoff_seed is null then null
        else s.playoff_seed <= sc.playoff_team_count
    end                                             as made_playoffs,

    s.wins,
    s.losses,
    s.ties,
    s.win_percentage,
    s.games_back,
    s.streak_length,
    s.streak_type,
    s.division_wins,
    s.division_losses,
    s.division_ties,

    s.platform_points,
    s.platform_points_adjusted,
    s.waiver_rank
from {{ ref('stg_team_standings') }} s
left join {{ ref('stg_divisions') }} d
    on  s.league_key  = d.league_key
    and s.season_year = d.season_year
    and s.division_id = d.division_id
left join {{ ref('stg_schedule_settings') }} sc
    on  s.league_key  = sc.league_key
    and s.season_year = sc.season_year
