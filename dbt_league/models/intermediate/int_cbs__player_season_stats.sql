-- int_cbs__player_season_stats.sql
-- Player-season stats on the CALCULATED_ lens, LONG form and stat_name-
-- bridged: one row per (league_key, cbs_player_id, season_year, stat_name).
-- The universal-sourced sibling of stg_cbs__player_season_stats (the
-- platform_ anchor, free-agent-only) -- THIS is the record book's stats
-- source, complete for every crosswalked player (Cole, Judge, the people
-- the platform archive silently lacks).
--
-- Three row families:
--   1. Bridged counting stats: stg_mlb__player_game canonicals summed per
--      player-season and translated canonical_key -> stat_name via the
--      stat_classification bridge (unbridged canonicals -- at_bats_against,
--      batters_faced... -- drop by design; the book speaks ESPN namespace).
--      games_played maps only from hitting-group rows: the ESPN G is
--      "Games Played by Batters," and group-scoping also keeps a
--      pre-2022 pitcher's batting games from double-counting his season
--      (GP, games pitched, carries the mound count via
--      appearances_pitching).
--   2. Derived counting stats the feed doesn't carry directly, exact
--      arithmetic of same-source sums: 1B = H - 2B - 3B - HR,
--      XBH = 2B + 3B + HR.
--   3. Engine rows from int_cbs__player_game_points: the per-game-derived
--      categories summed to season (QS, IRSTR, NH) and the priced lens
--      itself -- CALCULATED_POINTS / CALCULATED_HITTING_PTS /
--      CALCULATED_PITCHING_PTS, the season fantasy-point totals under
--      current CBS rules. For a two-discipline player the total blends
--      both (the Ohtani sentinels stay split by construction); the
--      reconciliation mart remains the universe-scoped platform-parity
--      view. PG deliberately absent -- the feed can't discriminate perfect
--      from merely no-hit (see the engine's derivation note).
--
-- POPULATION AND LENS (the honest label): crosswalked players' TOTAL MLB
-- production, seasons within the LEAGUE ERA (data-driven floor: the
-- league's own first season per the UI-history standings -- 2001 -- with
-- the platform archive's min as fallback; gamelogs reach 1991 but
-- pre-league seasons aren't league seasons). This is the interim "total"
-- lens -- production regardless of rostered/active status -- until
-- MLB-63's membership reconstruction scopes it. Two known biases, both
-- almanac-labeled: (a) early-era population coverage rides the MLB-63
-- UI-history crosswalk (year-end-roster names), so never-anchored
-- pass-throughs are absent; (b) a player's production counts even in
-- seasons nobody rostered them.
--
-- league_key fans from the engine/scoring settings -- each league's book
-- prices and scopes independently.

{{ config(materialized='view') }}

with archive_floor as (
    -- The league era the book covers. The UI-history standings carry the
    -- league's true first season (2001); the platform archive's min (2004)
    -- is the fallback for a league without a captured UI history.
    select min(first_season) as first_season
    from (
        select min(season_year) as first_season
        from {{ ref('stg_cbs__ui_standings') }}
        union all
        select min(season_year)
        from {{ ref('stg_cbs__player_season_stats') }}
    )
),

crosswalked_games as (
    select
        cw.cbs_player_id,
        cw.cbs_player_name,
        g.season_year,
        g.stat_group,
        g.canonical_key,
        g.stat_value
    from {{ ref('stg_mlb__player_game') }} g
    inner join {{ ref('stg_cbs__mlbam_crosswalk') }} cw
        on g.mlbam_id = cw.mlbam_id
        and (cw.stat_group_scope is null or cw.stat_group_scope = g.stat_group)
    cross join archive_floor f
    where g.season_year >= f.first_season
),

-- Family 1: canonical sums bridged to ESPN stat_names.
bridged as (
    select
        cg.cbs_player_id,
        max(cg.cbs_player_name)  as cbs_player_name,
        cg.season_year,
        sc.stat_name,
        sc.stat_category,
        sum(cg.stat_value)       as stat_value
    from crosswalked_games cg
    inner join {{ ref('stat_classification') }} sc
        on cg.canonical_key = sc.canonical_key
    where not (cg.canonical_key = 'games_played' and cg.stat_group = 'pitching')
    group by cg.cbs_player_id, cg.season_year, sc.stat_name, sc.stat_category
),

-- Family 2: exact single-source arithmetic the feed doesn't carry.
hit_shapes as (
    select
        cbs_player_id,
        max(cbs_player_name) as cbs_player_name,
        season_year,
        sum(case when canonical_key = 'hits'      then stat_value else 0 end) as h,
        sum(case when canonical_key = 'doubles'   then stat_value else 0 end) as doubles,
        sum(case when canonical_key = 'triples'   then stat_value else 0 end) as triples,
        sum(case when canonical_key = 'home_runs' then stat_value else 0 end) as hr
    from crosswalked_games
    where stat_group = 'hitting'
        and canonical_key in ('hits', 'doubles', 'triples', 'home_runs')
    group by cbs_player_id, season_year
),

derived as (
    select cbs_player_id, cbs_player_name, season_year,
           '1B' as stat_name, 'hitting' as stat_category,
           h - doubles - triples - hr as stat_value
    from hit_shapes
    union all
    select cbs_player_id, cbs_player_name, season_year,
           'XBH', 'hitting', doubles + triples + hr
    from hit_shapes
),

-- Family 3: the engine's per-game derivations and the priced lens, summed
-- to season. Already league-keyed and crosswalk-scoped; the archive floor
-- applies identically.
engine_seasons as (
    select
        p.league_key,
        p.cbs_player_id,
        max(p.cbs_player_name)         as cbs_player_name,
        p.season_year,
        sum(p.qs)                      as qs,
        sum(p.irstr)                   as irstr,
        sum(p.nh)                      as nh,
        {{ stable_sum("p.calculated_fpts", none) }}         as calculated_points,
        {{ stable_sum("p.calculated_hitting_pts", none) }}  as calculated_hitting_pts,
        {{ stable_sum("p.calculated_pitching_pts", none) }} as calculated_pitching_pts
    from {{ ref('int_cbs__player_game_points') }} p
    cross join archive_floor f
    where p.season_year >= f.first_season
    group by p.league_key, p.cbs_player_id, p.season_year
),

engine_long as (
    select league_key, cbs_player_id, cbs_player_name, season_year,
           'QS' as stat_name, 'pitching' as stat_category, qs as stat_value
    from engine_seasons
    union all
    select league_key, cbs_player_id, cbs_player_name, season_year,
           'IRSTR', 'pitching', irstr from engine_seasons
    union all
    select league_key, cbs_player_id, cbs_player_name, season_year,
           'NH', 'pitching', nh from engine_seasons
    union all
    select league_key, cbs_player_id, cbs_player_name, season_year,
           'CALCULATED_POINTS', 'total', calculated_points from engine_seasons
    union all
    select league_key, cbs_player_id, cbs_player_name, season_year,
           'CALCULATED_HITTING_PTS', 'hitting', calculated_hitting_pts from engine_seasons
    union all
    select league_key, cbs_player_id, cbs_player_name, season_year,
           'CALCULATED_PITCHING_PTS', 'pitching', calculated_pitching_pts from engine_seasons
),

-- Families 1+2 are platform-level; the league key fans from the engine's
-- league set so every league's book scopes independently.
leagues as (
    select distinct league_key from {{ ref('stg_cbs__scoring_settings') }}
)

select lg.league_key, b.cbs_player_id, b.cbs_player_name, b.season_year,
       b.stat_name, b.stat_category, b.stat_value
from bridged b cross join leagues lg
union all
select lg.league_key, d.cbs_player_id, d.cbs_player_name, d.season_year,
       d.stat_name, d.stat_category, d.stat_value
from derived d cross join leagues lg
union all
select league_key, cbs_player_id, cbs_player_name, season_year,
       stat_name, stat_category, stat_value
from engine_long
