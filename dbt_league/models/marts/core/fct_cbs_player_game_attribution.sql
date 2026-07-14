-- fct_cbs_player_game_attribution.sql
-- THE HISTORIC ACTIVE LENS (MLB-63): every priced player-game
-- (int_cbs__player_game_points) attributed to the franchise that
-- rostered the player that day, with the active-state weight and --
-- per Kyle's rule -- a PER-ROW provenance flag that says how we know:
--
--   captured               2026: the daily roster captures (deployed
--                          slot + A/RS as CBS served them). Confirmed.
--   reconstructed_day      2001-2003, 2021-2025: membership from the
--                          stint walk-back AND the day's active state
--                          from the lineup-log intervals. Confirmed
--                          (reconstruction-grade).
--   estimated_startshare   2004-2020: membership from the walk-back;
--                          active WEIGHT estimated as the player's global
--                          Start%/Own% ratio from ANY of that season's
--                          year-end anchors (the rates are player-season
--                          stats, not franchise stats -- Kyle 2026-07-14 --
--                          so a mid-season Team A stint borrows the ratio
--                          he finished with on Team B). ESTIMATE.
--   estimated_membership   2004-2020 stints with no usable estimator
--                          (own_pct 0/null, or the player on NO year-end
--                          anchor that season -- dropped and out of the
--                          league by year's end, the season-ending-injury
--                          class): membership confirmed, activity unknown
--                          -- weight NULL. The interactive surface filters
--                          these; the almanac explains them.
--
-- is_active is boolean where the day's state is KNOWN, NULL where
-- estimated; active_weight is the scoring weight either way (1/0 or
-- the estimator). Rows where nobody rostered the player (FA games)
-- don't exist here by construction. Ambiguity and inferred ends ride
-- as flags (is_ambiguous_name, membership_end_inferred), never as
-- silent guesses.
--
-- Grain: one row per (league_key, cbs_player_id, stat_group, game_pk,
-- game_date, game_index) -- the engine's grain, franchise-attributed.

{{ config(materialized='table') }}

with games as (
    select * from {{ ref('int_cbs__player_game_points') }}
),

-- ---------------------------------------------------------------- 2026
captured as (
    select
        g.league_key,
        g.cbs_player_id,
        g.cbs_player_name,
        g.mlbam_id,
        g.season_year,
        g.stat_group,
        g.game_date,
        g.game_pk,
        g.game_index,
        r.team_id::integer               as franchise_id,
        r.team_name,
        'captured'                       as provenance,
        'captured'                       as state_source,
        (r.roster_status = 'A')          as is_active,
        iff(r.roster_status = 'A', 1.0, 0.0) as active_weight,
        false                            as is_ambiguous_name,
        false                            as membership_end_inferred,
        false                            as attribution_contested,
        g.calculated_fpts,
        g.calculated_hitting_pts,
        g.calculated_pitching_pts
    from games g
    inner join {{ ref('stg_cbs__rosters') }} r
        on g.league_key = r.league_key
        and g.cbs_player_id = r.player_id
        and g.game_date = r.roster_date
),

-- ------------------------------------------------- 2001-2025 walk-back
stints as (
    select * from {{ ref('int_cbs__roster_stints_effective') }}
),

anchors_est as (
    select
        league_key, season_year, franchise_id,
        {{ cbs_name_key('player_name_raw') }} as name_key,
        max(est_start_share)                  as est_start_share,
        max(primary_pos)                      as primary_pos,
        max(roster_status)                    as anchor_status
    from {{ ref('stg_cbs__ui_rosters') }}
    group by 1, 2, 3, 4
),

-- The estimator at SEASON grain (Kyle, 2026-07-14): Own%/Start% are global
-- CBS stats about the PLAYER, not the franchise, so the ratio from any
-- year-end anchor row covers every stint that season -- a mid-season stint
-- on Team A borrows the ratio the player finished with on Team B.
-- (Franchise-scoping the estimator was needless caution; it silently
-- zeroed ~4% of 2004-2020 production. anchor_STATUS stays franchise-
-- scoped above: a year-end A/RS on Team B says nothing about Team A.)
anchors_est_season as (
    select
        league_key, season_year,
        {{ cbs_name_key('player_name_raw') }} as name_key,
        max(est_start_share)                  as est_start_share
    from {{ ref('stg_cbs__ui_rosters') }}
    group by 1, 2, 3
),

reconstructed as (
    select
        g.league_key,
        g.cbs_player_id,
        g.cbs_player_name,
        g.mlbam_id,
        g.season_year,
        g.stat_group,
        g.game_date,
        g.game_pk,
        g.game_index,
        s.franchise_id,
        cast(null as varchar)            as team_name,
        case
            when li.state is not null        then 'reconstructed_day'
            when s.season_year between 2004 and 2020
                 and aes.est_start_share is not null
                                             then 'estimated_startshare'
            when s.season_year between 2004 and 2020
                                             then 'estimated_membership'
            when ae.anchor_status is not null then 'reconstructed_day'
            else 'estimated_membership'   -- lineup era, no events, no anchor
        end                              as provenance,
        case
            when li.state is not null        then li.state_source
            when s.season_year between 2004 and 2020
                 and aes.est_start_share is not null
                                             then 'startshare'
            when s.season_year not between 2004 and 2020
                 and ae.anchor_status is not null
                                             then 'anchor_hold'
        end                              as state_source,
        case
            when li.state is not null        then li.state = 'A'
            when s.season_year between 2004 and 2020
                                             then null
            when ae.anchor_status is not null then ae.anchor_status = 'A'
        end                              as is_active,
        case
            when li.state is not null        then iff(li.state = 'A', 1.0, 0.0)
            when s.season_year between 2004 and 2020
                                             then aes.est_start_share
            when ae.anchor_status is not null then iff(ae.anchor_status = 'A', 1.0, 0.0)
        end                              as active_weight,
        coalesce(s.is_ambiguous_name, false)               as is_ambiguous_name,
        (s.was_truncated or coalesce(s.missing_departure, false))
                                                           as membership_end_inferred,
        (count(*) over (
            partition by g.league_key, g.cbs_player_id, g.stat_group,
                         g.game_pk, g.game_date, g.game_index) > 1)
                                                           as attribution_contested,
        g.calculated_fpts,
        g.calculated_hitting_pts,
        g.calculated_pitching_pts
    from games g
    inner join stints s
        on g.league_key = s.league_key
        and g.season_year = s.season_year
        and {{ cbs_name_key('g.cbs_player_name') }} = s.name_key
        and g.game_date >= s.stint_start
        and g.game_date < s.attribution_end_exclusive
    left join {{ ref('int_cbs__lineup_intervals') }} li
        on s.league_key = li.league_key
        and s.season_year = li.season_year
        and s.franchise_id = li.franchise_id
        and s.name_key = li.name_key
        and g.game_date >= li.state_start
        and g.game_date < li.state_end_exclusive
    left join anchors_est ae
        on s.league_key = ae.league_key
        and s.season_year = ae.season_year
        and s.franchise_id = ae.franchise_id
        and s.name_key = ae.name_key
    left join anchors_est_season aes
        on s.league_key = aes.league_key
        and s.season_year = aes.season_year
        and s.name_key = aes.name_key
    where g.season_year between 2001 and 2025
    -- One attribution per game row, ALWAYS: an ambiguous name (two real
    -- players sharing it) can match two teams' stints -- prefer the
    -- stint whose anchored position matches the game's discipline
    -- (catcher games don't credit the reliever of the same name), then
    -- the most recent stint. Losers surface via attribution_contested.
    qualify row_number() over (
        partition by g.league_key, g.cbs_player_id, g.stat_group,
                     g.game_pk, g.game_date, g.game_index
        order by iff(
            case when ae.primary_pos in ('SP', 'RP', 'P')
                 then g.stat_group = 'pitching'
                 else g.stat_group = 'hitting' end, 0, 1),
            s.stint_start desc
    ) = 1
)

select * from captured
union all
select * from reconstructed
