-- mart_stat_leaderboard.sql
-- Top-10 leaderboard across team AND player grains, for both stat-level
-- (HR, K, RBI, etc.) and score-level (total_points, hitting_points,
-- pitching_points) columns.
--
-- Implementation uses Snowflake UNPIVOT to fold wide columns from
-- fct_weekly_team_performance and fct_weekly_player_performance back into
-- (stat_name, stat_value) long format, then ranks uniformly. UNPIVOT is
-- Snowflake-specific; if the project ever moves to a different warehouse
-- (e.g. DuckDB for a local-CLI build), this can be rewritten as an explicit
-- UNION ALL per stat column -- tedious but portable.
--
-- Grain: (entity_grain, stat_name, record_scope, rank). entity_grain in
-- {'team', 'player'}. record_scope in {'all_time', 'current_season'}.
-- Rank 1..10 per (entity_grain, stat_name, record_scope).
--
-- Excludes abnormal matchup periods via matchup_schedule.is_abnormal = false.
-- Ties broken by recency (newer season_year, then newer matchup_period).
-- View materialization -- rankings are retroactively mutable so incremental
-- would be fragile. Zero storage, always fresh.

{{ config(materialized='view') }}

with team_source as (
    select
        t.season_year,
        t.matchup_period,
        t.team_id,
        t.team_name,
        t.owner_name,
        t.h, t.ab, t.b_bb, t.b_so, t.hbp, t.sf, t.hr, t.r, t.rbi,
        t.sb, t.cs, t.tb, t.singles, t.doubles, t.triples, t.xbh,
        t.w, t.l, t.k, t.er, t.outs, t.qs, t.sv, t.hld,
        t.p_h, t.p_bb, t.p_hr, t.p_r, t.cg, t.blk, t.wp,
        t.total_points, t.hitting_points, t.pitching_points
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
        total_points, hitting_points, pitching_points
    ))
),

player_source as (
    select
        p.season_year,
        p.matchup_period,
        p.team_id,
        p.team_name,
        p.owner_name,
        p.player_id,
        p.player_name,
        p.display_name,
        p.h, p.ab, p.b_bb, p.b_so, p.hbp, p.sf, p.hr, p.r, p.rbi,
        p.sb, p.cs, p.tb, p.singles, p.doubles, p.triples, p.xbh,
        p.w, p.l, p.k, p.er, p.outs, p.qs, p.sv, p.hld,
        p.p_h, p.p_bb, p.p_hr, p.p_r, p.cg, p.blk, p.wp,
        p.total_points, p.hitting_points, p.pitching_points
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
        total_points, hitting_points, pitching_points
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

all_time_ranked as (
    select
        'all_time'::varchar as record_scope,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value desc, season_year desc, matchup_period desc
        ) as rank
    from combined c
),

current_season_ranked as (
    select
        'current_season'::varchar as record_scope,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value desc, season_year desc, matchup_period desc
        ) as rank
    from combined c
    where c.season_year = (select y from current_year)
)

select * from all_time_ranked       where rank <= 10
union all
select * from current_season_ranked where rank <= 10
