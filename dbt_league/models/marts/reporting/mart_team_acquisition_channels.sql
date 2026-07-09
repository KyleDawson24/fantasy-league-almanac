-- mart_team_acquisition_channels.sql
-- MLB-17: the acquisition-channel reporting surface -- one wide row per team
-- with the points its roster produced, split by how each player arrived
-- (Keeper / Draft / Trade / FA Add) and the points it forfeited, split by how
-- each departed player left (Dropped / Traded Away), under two lenses.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id).
-- ==========================================================================
--
-- Built over fct_roster_stints (the stint intervals + open/close channels) and
-- fct_player_daily_performance (daily production). Two lenses, same shape:
--
--   ACTIVE lens    Acquired = active points a player scored FOR the team while
--                  on it, bucketed by the stint's open_channel. Lost = active
--                  points a departed player produced FOR OTHER TEAMS while off
--                  this roster (a drop nobody harvests costs nothing here),
--                  bucketed by the stint's close_type.
--   ROSTERED lens  Acquired = ALL points (active + bench/IL) while on the
--                  roster. Lost = ALL production onward -- other teams AND
--                  unowned -- while off the roster.
--
-- Lost window (per closed stint): the periods after the stint ends until the
-- player's NEXT stint on the SAME team (re-acquisition pauses the lost clock)
-- or season end. Keyed on player, so the thrice-dropped guy is never double
-- counted -- each gap is attributed to the departure that opened it.
--
-- Deltas: FA delta = FA-add acquired minus dropped-away lost; Trade delta =
-- trade acquired minus traded-away lost. Both can go negative (polarity-aware
-- in the tab). Points rounded once at this grain (float-sum determinism, same
-- rationale as mart_team_season_standings).
--
-- Materialization: table (float sums feed the almanac byte-diff goldens).

{{ config(materialized='table') }}

with stints as (
    select * from {{ ref('fct_roster_stints') }}
),

daily as (
    select
        league_key,
        season_year,
        scoring_period,
        team_id,
        player_id,
        performance_status,
        total_stat_pts
    from {{ ref('fct_player_daily_performance') }}
),

-- ACQUIRED: production earned FOR the team during each stint, by open_channel.
acquired as (
    select
        s.league_key,
        s.season_year,
        s.team_id,
        s.open_channel as channel,
        round(sum(case when d.performance_status = 'active'
                       then d.total_stat_pts else 0 end), 1) as active_pts,
        round(sum(d.total_stat_pts), 1)                      as rostered_pts
    from stints s
    join daily d
        on  d.league_key  = s.league_key
        and d.season_year = s.season_year
        and d.player_id   = s.player_id
        and d.team_id     = s.team_id
        and d.scoring_period between s.stint_start_period and s.stint_end_period
    group by 1, 2, 3, 4
),

-- The next stint on the SAME team bounds the lost window (computed over ALL
-- stints, before filtering to closed ones, so an open-ended re-acquisition
-- still caps the clock).
stint_seq as (
    select
        s.*,
        lead(stint_start_period) over (
            partition by league_key, season_year, player_id, team_id
            order by stint_start_period
        ) as next_same_team_start
    from stints s
),

closed_stints as (
    select * from stint_seq where close_type is not null
),

season_max as (
    select league_key, season_year, max(scoring_period) as max_period
    from daily group by 1, 2
),

-- LOST: production a departed player made while off this roster, by close_type.
lost as (
    select
        c.league_key,
        c.season_year,
        c.team_id,
        c.close_type as channel,
        round(sum(case when d.performance_status = 'active'
                        and d.team_id is not null
                        and d.team_id <> c.team_id
                       then d.total_stat_pts else 0 end), 1) as active_pts,
        round(sum(d.total_stat_pts), 1)                      as rostered_pts
    from closed_stints c
    join season_max sm
        on sm.league_key = c.league_key and sm.season_year = c.season_year
    join daily d
        on  d.league_key  = c.league_key
        and d.season_year = c.season_year
        and d.player_id   = c.player_id
        and d.scoring_period >  c.stint_end_period
        and d.scoring_period <  coalesce(c.next_same_team_start, sm.max_period + 1)
    group by 1, 2, 3, 4
),

combined as (
    select league_key, season_year, team_id, channel, active_pts, rostered_pts from acquired
    union all
    select league_key, season_year, team_id, channel, active_pts, rostered_pts from lost
),

pivoted as (
    select
        league_key,
        season_year,
        team_id,
        -- Acquired, active lens
        sum(iff(channel = 'KEEPER',      active_pts, 0)) as keeper_active_pts,
        sum(iff(channel = 'DRAFT',       active_pts, 0)) as draft_active_pts,
        sum(iff(channel = 'TRADE',       active_pts, 0)) as trade_active_pts,
        sum(iff(channel = 'FA_ADD',      active_pts, 0)) as fa_add_active_pts,
        -- Lost, active lens
        sum(iff(channel = 'DROPPED',     active_pts, 0)) as dropped_active_pts,
        sum(iff(channel = 'TRADED_AWAY', active_pts, 0)) as traded_away_active_pts,
        -- Acquired, rostered lens
        sum(iff(channel = 'KEEPER',      rostered_pts, 0)) as keeper_rostered_pts,
        sum(iff(channel = 'DRAFT',       rostered_pts, 0)) as draft_rostered_pts,
        sum(iff(channel = 'TRADE',       rostered_pts, 0)) as trade_rostered_pts,
        sum(iff(channel = 'FA_ADD',      rostered_pts, 0)) as fa_add_rostered_pts,
        -- Lost, rostered lens
        sum(iff(channel = 'DROPPED',     rostered_pts, 0)) as dropped_rostered_pts,
        sum(iff(channel = 'TRADED_AWAY', rostered_pts, 0)) as traded_away_rostered_pts
    from combined
    group by 1, 2, 3
),

-- Team universe + latest labels (every team that fielded a roster this season).
team_labels as (
    select
        league_key,
        season_year,
        team_id,
        max_by(team_name,   scoring_period) as team_name,
        max_by(team_abbrev, scoring_period) as team_abbrev
    from {{ ref('fct_player_daily_performance') }}
    where team_id is not null
    group by 1, 2, 3
),

teams as (
    select distinct league_key, season_year, team_id
    from stints
)

select
    t.league_key,
    t.season_year,
    t.team_id,
    tl.team_name,
    tl.team_abbrev,
    tod.owner_display,

    -- ACTIVE lens ---------------------------------------------------------
    coalesce(p.keeper_active_pts,      0) as keeper_active_pts,
    coalesce(p.draft_active_pts,       0) as draft_active_pts,
    coalesce(p.trade_active_pts,       0) as trade_active_pts,
    coalesce(p.fa_add_active_pts,      0) as fa_add_active_pts,
    round(coalesce(p.keeper_active_pts, 0) + coalesce(p.draft_active_pts, 0)
        + coalesce(p.trade_active_pts, 0) + coalesce(p.fa_add_active_pts, 0), 1)
                                          as acquired_active_pts,
    coalesce(p.dropped_active_pts,     0) as dropped_active_pts,
    coalesce(p.traded_away_active_pts, 0) as traded_away_active_pts,
    round(coalesce(p.dropped_active_pts, 0) + coalesce(p.traded_away_active_pts, 0), 1)
                                          as lost_active_pts,
    round(coalesce(p.fa_add_active_pts, 0) - coalesce(p.dropped_active_pts, 0), 1)
                                          as fa_delta_active_pts,
    round(coalesce(p.trade_active_pts, 0) - coalesce(p.traded_away_active_pts, 0), 1)
                                          as trade_delta_active_pts,

    -- ROSTERED lens -------------------------------------------------------
    coalesce(p.keeper_rostered_pts,      0) as keeper_rostered_pts,
    coalesce(p.draft_rostered_pts,       0) as draft_rostered_pts,
    coalesce(p.trade_rostered_pts,       0) as trade_rostered_pts,
    coalesce(p.fa_add_rostered_pts,      0) as fa_add_rostered_pts,
    round(coalesce(p.keeper_rostered_pts, 0) + coalesce(p.draft_rostered_pts, 0)
        + coalesce(p.trade_rostered_pts, 0) + coalesce(p.fa_add_rostered_pts, 0), 1)
                                            as acquired_rostered_pts,
    coalesce(p.dropped_rostered_pts,     0) as dropped_rostered_pts,
    coalesce(p.traded_away_rostered_pts, 0) as traded_away_rostered_pts,
    round(coalesce(p.dropped_rostered_pts, 0) + coalesce(p.traded_away_rostered_pts, 0), 1)
                                            as lost_rostered_pts,
    round(coalesce(p.fa_add_rostered_pts, 0) - coalesce(p.dropped_rostered_pts, 0), 1)
                                            as fa_delta_rostered_pts,
    round(coalesce(p.trade_rostered_pts, 0) - coalesce(p.traded_away_rostered_pts, 0), 1)
                                            as trade_delta_rostered_pts

from teams t
left join pivoted p using (league_key, season_year, team_id)
left join team_labels tl using (league_key, season_year, team_id)
left join {{ ref('dim_team_owner') }} tod using (league_key, season_year, team_id)
