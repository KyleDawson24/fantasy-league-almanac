-- mart_team_points_reconciliation.sql
-- THE WALK-BACK'S REPORT CARD (MLB-63): reconstructed team-season
-- points vs the OFFICIAL final standings -- 25 seasons x the league's
-- teams of ground truth, one row per (league_key, season_year,
-- franchise_id). This is the mart that grades the entire
-- reconstruction: the official total is what CBS actually awarded; the
-- reconstructed active-lens total is what the walk-back attributes.
--
-- Reading the delta:
--   - Small |delta|: the reconstruction (membership + active state +
--     calculated scoring) reproduces the season.
--   - Known systematic piece: CBS's sparse pre-2023 IRSTR under-counts
--     the OFFICIAL side (the MLB-62 finding) -- reconstructed runs a
--     touch HOT on pitching-heavy rosters in those years.
--   - The estimated era (2004-2020) rides est_start_share weights, so
--     its deltas measure the ESTIMATOR as much as the membership.
--   - unattributed_fpts: production by rostered players whose activity
--     weight is UNKNOWN (estimated_membership provenance) -- what the
--     weighted total cannot see. rostered_fpts (all production while
--     rostered, weight-blind) bounds the season from above.
--
-- Provenance mix columns say HOW each season-franchise total was
-- earned; the grade is derivable, the flags are the truth.

{{ config(materialized='table') }}

with attribution as (
    select * from {{ ref('fct_cbs_player_game_attribution') }}
    where season_year between 2001 and 2025
),

reconstructed as (
    select
        league_key,
        season_year,
        franchise_id,
        round(sum(calculated_fpts * coalesce(active_weight, 0)), 1)
            as reconstructed_active_fpts,
        round(sum(calculated_fpts), 1)      as rostered_fpts,
        round(sum(iff(active_weight is null, calculated_fpts, 0)), 1)
            as unattributed_fpts,
        round(sum(iff(provenance = 'captured' or provenance = 'reconstructed_day',
                      calculated_fpts * coalesce(active_weight, 0), 0)), 1)
            as confirmed_active_fpts,
        round(sum(iff(provenance = 'estimated_startshare',
                      calculated_fpts * coalesce(active_weight, 0), 0)), 1)
            as estimated_active_fpts,
        count(distinct cbs_player_name)     as players_attributed
    from attribution
    group by 1, 2, 3
),

official as (
    select
        league_key,
        season_year,
        franchise_id,
        team_name,
        standings_rank,
        is_champion,
        total_points as official_total_points
    from {{ ref('stg_cbs__ui_standings') }}
)

select
    o.league_key,
    o.season_year,
    o.franchise_id,
    o.team_name,
    o.standings_rank,
    o.is_champion,
    o.official_total_points,
    r.reconstructed_active_fpts,
    r.reconstructed_active_fpts - o.official_total_points as fpts_delta,
    round(div0(r.reconstructed_active_fpts - o.official_total_points,
               o.official_total_points) * 100, 2)         as fpts_delta_pct,
    r.rostered_fpts,
    r.unattributed_fpts,
    r.confirmed_active_fpts,
    r.estimated_active_fpts,
    r.players_attributed
from official o
left join reconstructed r
    on o.league_key = r.league_key
    and o.season_year = r.season_year
    and o.franchise_id = r.franchise_id
order by o.season_year, o.standings_rank
