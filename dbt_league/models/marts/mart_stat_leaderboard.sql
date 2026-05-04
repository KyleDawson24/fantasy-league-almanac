-- mart_stat_leaderboard.sql
-- Top-10 leaderboard across team AND player grains, for both stat-level
-- (HR, K, RBI, etc.) and score-level columns. Both platform_* (ESPN's
-- official tally at the time) and calculated_* (rules-normalized under
-- current weights) score columns are included so consumers can choose
-- which lens to rank by. Phase 5 surfaces calculated_* in the records
-- output; platform_* remains available for cross-season comparisons.
--
-- Phase 5 (#3): added `record_direction` dimension. The mart now emits
-- both 'best' (rank by stat_value DESC -- the original behavior) and
-- 'worst' (rank by stat_value ASC) rankings, top-10 each. Consumers
-- select the direction they want; the new-records detection in
-- generate_summary.py uses both, with consumer-side polarity filtering
-- (negative-weighted stats only surface 'best'/most-of, positive-weighted
-- stats surface both).
--
-- Implementation uses Snowflake UNPIVOT to fold wide columns from
-- fct_weekly_team_performance and fct_weekly_player_performance back into
-- (stat_name, stat_value) long format, then ranks uniformly. UNPIVOT is
-- Snowflake-specific; if the project ever moves to a different warehouse
-- (e.g. DuckDB for a local-CLI build), this can be rewritten as an explicit
-- UNION ALL per stat column -- tedious but portable.
--
-- Grain: (entity_grain, stat_name, record_scope, record_direction, rank).
-- entity_grain in {'team', 'player'}. record_scope in {'all_time',
-- 'current_season'}. record_direction in {'best', 'worst'}. Rank 1..10
-- per (entity_grain, stat_name, record_scope, record_direction).
--
-- Excludes abnormal matchup periods via matchup_schedule.is_abnormal = false.
-- Ties broken by recency (newer season_year, then newer matchup_period) in
-- BOTH directions -- so a team that just tied a record is rank 1 either way.
-- View materialization -- rankings are retroactively mutable so incremental
-- would be fragile. Zero storage, always fresh.

{{ config(materialized='view') }}

with team_source as (
    select
        t.season_year,
        t.matchup_period,
        t.team_id,
        t.team_name,
        t.team_abbrev,
        t.owner_name,
        t.h, t.ab, t.b_bb, t.b_so, t.hbp, t.sf, t.hr, t.r, t.rbi,
        t.sb, t.cs, t.tb, t.singles, t.doubles, t.triples, t.xbh,
        t.w, t.l, t.k, t.er, t.outs, t.qs, t.sv, t.hld,
        t.p_h, t.p_bb, t.p_hr, t.p_r, t.cg, t.blk, t.wp,
        t.platform_points, t.platform_hitting_pts, t.platform_pitching_pts,
        t.calculated_points, t.calculated_hitting_pts, t.calculated_pitching_pts
    from {{ ref('fct_weekly_team_performance') }} t
    inner join {{ ref('matchup_schedule') }} s
        on t.season_year = s.season_year
        and t.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

team_unpivoted as (
    select
        'team'::varchar                as entity_grain,
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        null::integer                  as player_id,
        null::varchar                  as player_name,
        null::varchar                  as display_name,
        stat_name,
        stat_value
    from team_source
    unpivot (stat_value for stat_name in (
        h, ab, b_bb, b_so, hbp, sf, hr, r, rbi,
        sb, cs, tb, singles, doubles, triples, xbh,
        w, l, k, er, outs, qs, sv, hld,
        p_h, p_bb, p_hr, p_r, cg, blk, wp,
        platform_points, platform_hitting_pts, platform_pitching_pts,
        calculated_points, calculated_hitting_pts, calculated_pitching_pts
    ))
),

player_source as (
    select
        p.season_year,
        p.matchup_period,
        p.team_id,
        p.team_name,
        p.team_abbrev,
        p.owner_name,
        p.player_id,
        p.player_name,
        p.display_name,
        p.h, p.ab, p.b_bb, p.b_so, p.hbp, p.sf, p.hr, p.r, p.rbi,
        p.sb, p.cs, p.tb, p.singles, p.doubles, p.triples, p.xbh,
        p.w, p.l, p.k, p.er, p.outs, p.qs, p.sv, p.hld,
        p.p_h, p.p_bb, p.p_hr, p.p_r, p.cg, p.blk, p.wp,
        p.platform_points, p.platform_hitting_pts, p.platform_pitching_pts,
        p.calculated_points, p.calculated_hitting_pts, p.calculated_pitching_pts
    from {{ ref('fct_weekly_player_performance') }} p
    inner join {{ ref('matchup_schedule') }} s
        on p.season_year = s.season_year
        and p.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

player_unpivoted as (
    select
        'player'::varchar  as entity_grain,
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        player_id,
        player_name,
        display_name,
        stat_name,
        stat_value
    from player_source
    unpivot (stat_value for stat_name in (
        h, ab, b_bb, b_so, hbp, sf, hr, r, rbi,
        sb, cs, tb, singles, doubles, triples, xbh,
        w, l, k, er, outs, qs, sv, hld,
        p_h, p_bb, p_hr, p_r, cg, blk, wp,
        platform_points, platform_hitting_pts, platform_pitching_pts,
        calculated_points, calculated_hitting_pts, calculated_pitching_pts
    ))
),

combined as (
    select * from team_unpivoted
    union all
    select * from player_unpivoted
),

current_year as (
    select max(season_year) as y from combined
),

-- Four rank dimensions: {all_time, current_season} x {best, worst}.
-- Each computes top-10 in its direction; combined output has a
-- record_direction column distinguishing best from worst.

all_time_best as (
    select
        'all_time'::varchar as record_scope,
        'best'::varchar     as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value desc, season_year desc, matchup_period desc
        ) as rank
    from combined c
),

all_time_worst as (
    select
        'all_time'::varchar as record_scope,
        'worst'::varchar    as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value asc, season_year desc, matchup_period desc
        ) as rank
    from combined c
),

current_season_best as (
    select
        'current_season'::varchar as record_scope,
        'best'::varchar           as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value desc, season_year desc, matchup_period desc
        ) as rank
    from combined c
    where c.season_year = (select y from current_year)
),

current_season_worst as (
    select
        'current_season'::varchar as record_scope,
        'worst'::varchar          as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value asc, season_year desc, matchup_period desc
        ) as rank
    from combined c
    where c.season_year = (select y from current_year)
)

select * from all_time_best         where rank <= 10
union all
select * from all_time_worst        where rank <= 10
union all
select * from current_season_best   where rank <= 10
union all
select * from current_season_worst  where rank <= 10
