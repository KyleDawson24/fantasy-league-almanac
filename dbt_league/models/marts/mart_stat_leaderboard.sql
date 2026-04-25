-- mart_stat_leaderboard.sql
-- Top-10 leaderboard per stat per scope. Consumer-facing records contract.
-- Materialized as a view: always fresh (rankings are retroactively mutable,
-- so incremental would be fragile), zero storage, cheap to recompute.
--
-- Grain: (stat_name, record_scope, rank) where rank in 1..10.
-- Scopes:
--   all_time        - records across every loaded season
--   current_season  - records within the current (max) season only
--
-- Excludes abnormal matchup periods (opening week, All-Star break) via the
-- matchup_schedule seed. Playoffs are included (is_abnormal = false).

{{ config(materialized='view') }}

with team_stats as (
    select t.*
    from {{ ref('fct_weekly_team_stats') }} t
    inner join {{ ref('matchup_schedule') }} s
        on t.season_year = s.season_year
        and t.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

current_year as (
    select max(season_year) as y from team_stats
),

all_time_ranked as (
    select
        'all_time'::varchar as record_scope,
        season_year,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        stat_name,
        stat_category,
        stat_value,
        row_number() over (
            partition by stat_name
            order by stat_value desc, season_year desc, matchup_period desc
        ) as rank
    from team_stats
),

current_season_ranked as (
    select
        'current_season'::varchar as record_scope,
        season_year,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        stat_name,
        stat_category,
        stat_value,
        row_number() over (
            partition by stat_name
            order by stat_value desc, season_year desc, matchup_period desc
        ) as rank
    from team_stats
    where season_year = (select y from current_year)
)

select * from all_time_ranked       where rank <= 10
union all
select * from current_season_ranked where rank <= 10
