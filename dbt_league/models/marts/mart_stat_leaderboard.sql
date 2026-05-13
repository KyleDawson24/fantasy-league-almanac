-- mart_stat_leaderboard.sql
-- Top-10 leaderboard across team AND player grains, active AND inactive
-- performance, for stat-level (HR, K, RBI, etc.) and score-level columns.
--
-- Phase 7 F rewrite: previously a hand-maintained UNPIVOT list across two
-- source CTEs (team + player active). Now seed-driven via a compile-time
-- Jinja loop over stat_classification (where is_record_candidate = true),
-- and structured as four source CTEs (one per fact in the active/inactive
-- x team/player matrix) UNION ALL'd into a single combined CTE before the
-- UNPIVOT and ranking pass.
--
-- New `performance_status` partition column ('active' | 'inactive')
-- segregates the rankings: team-active HR records rank separately from
-- team-inactive HR records, etc. Consumers default-filter to
-- performance_status = 'active' for v1.0; inactive records exist for
-- ad-hoc analysis but aren't surfaced in any output script.
--
-- Source-CTE column-alignment rules (each rule applies via Jinja-conditional
-- inside the per-source loop; the loop itself iterates one canonical list
-- from the seed):
--   - team_active   : counting + derived + rates + platform_* + calc_*
--                     populated. wasted_points NULL.
--   - team_inactive : counting + derived + calc_* populated. Rates NULL,
--                     platform_* NULL. wasted_points populated for
--                     ROSTERED_INACTIVE rows only (calculated_points
--                     value); NULL for FA rows -- preserves today's
--                     "wasted_points doesn't include FA pool" semantic.
--   - player_active : counting + derived + platform_* + calc_* populated.
--                     Rates NULL (Path A: single-IP relief produces
--                     27.00 WHIP / 243 ERA values that dominate without
--                     signal; drop at mart). wasted_points NULL.
--   - player_inactive: counting + derived + calc_* populated. Rates,
--                     platform_*, wasted_points all NULL.
--
-- UNPIVOT's default EXCLUDE NULLS handles the NULL-padded slots --
-- they simply don't emit leaderboard rows for those (entity_grain,
-- stat_name, performance_status) combinations.
--
-- Implementation uses Snowflake UNPIVOT to fold wide columns back into
-- (stat_name, stat_value) long format, then ranks uniformly. UNPIVOT
-- is Snowflake-specific; if the project ever moves to a different
-- warehouse, this can be rewritten as explicit UNION ALL per stat
-- column -- tedious but portable.
--
-- Grain: (entity_grain, performance_status, stat_name, record_scope,
-- record_direction, rank). entity_grain in {'team', 'player'};
-- performance_status in {'active', 'inactive'}; record_scope in
-- {'all_time', 'current_season'}; record_direction in {'most', 'fewest'}.
-- Rank 1..10 per partition.
--
-- Excludes abnormal matchup periods via matchup_schedule.is_abnormal = false.
-- Ties broken by recency (newer season_year, then newer matchup_period)
-- followed by team_id then player_id (Phase 7 B1 deterministic tiebreak).
--
-- View materialization -- rankings are retroactively mutable so
-- incremental would be fragile. Zero storage, always fresh.

{{ config(materialized='view') }}

-- ----------------------------------------------------------------------
-- Compile-time fetch of the stat universe from the seed. is_record_
-- candidate=true returns exactly the 56 leaderboard columns (39 raw
-- counting + 4 derived + 6 rate + 1 wasted + 6 score). The CASE applies
-- the seed -> leaderboard name translation for stats whose names differ
-- (1B -> SINGLES, 64 -> SHO). Duplicates stat_catalog.SEED_TO_LEADERBOARD
-- — keep both in sync. Phase 7 F-prep excluded stat '30' (Hit for the
-- Cycle) since no wide '30' column exists yet on any fact.
--
-- run_query executes at compile time; the loop unrolls into static SQL
-- before Snowflake sees it. `execute` guard yields an empty list during
-- the parse phase so model parsing doesn't require the seed to exist.
{% set stat_query %}
    select
        case stat_name
            when '1B' then 'SINGLES'
            when '2B' then 'DOUBLES'
            when '3B' then 'TRIPLES'
            when '64' then 'SHO'
            else stat_name
        end as leaderboard_name,
        derivation_expr
    from {{ ref('stat_classification') }}
    where is_record_candidate = true
    order by 1
{% endset %}

{% set stat_results = run_query(stat_query) %}
{% if execute %}
    {% set stats = stat_results.rows %}
{% else %}
    {% set stats = [] %}
{% endif %}


with current_year as (
    select max(season_year) as y from {{ source('raw', 'box_scores') }}
),

team_active_source as (
    select
        'team'::varchar     as entity_grain,
        'active'::varchar   as performance_status,
        null::varchar       as wasted_bucket,
        t.season_year,
        t.matchup_period,
        t.team_id,
        t.team_name,
        t.team_abbrev,
        t.owner_name,
        null::integer       as player_id,
        null::varchar       as player_name,
        null::varchar       as display_name
        {%- for row in stats -%}
            {%- set name = row[0] -%}
            {%- set deriv = row[1] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        null::float                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        t.{{ lc }}                 as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_weekly_team_active_performance') }} t
    inner join {{ ref('matchup_schedule') }} s
        on t.season_year = s.season_year
        and t.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

team_inactive_source as (
    select
        'team'::varchar     as entity_grain,
        'inactive'::varchar as performance_status,
        ti.wasted_bucket,
        ti.season_year,
        ti.matchup_period,
        ti.team_id,
        ti.team_name,
        ti.team_abbrev,
        ti.owner_name,
        null::integer       as player_id,
        null::varchar       as player_name,
        null::varchar       as display_name
        {%- for row in stats -%}
            {%- set name = row[0] -%}
            {%- set deriv = row[1] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        case when ti.wasted_bucket = 'ROSTERED_INACTIVE'
             then ti.calculated_points else null end as {{ lc }}
        {%- elif name in ['ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'] -%}
        null::float                as {{ lc }}
        {%- elif name in ['PLATFORM_POINTS', 'PLATFORM_HITTING_PTS', 'PLATFORM_PITCHING_PTS'] -%}
        null::float                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        ti.{{ lc }}                as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_weekly_team_inactive_performance') }} ti
    inner join {{ ref('matchup_schedule') }} s
        on ti.season_year = s.season_year
        and ti.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

player_active_source as (
    select
        'player'::varchar   as entity_grain,
        'active'::varchar   as performance_status,
        null::varchar       as wasted_bucket,
        p.season_year,
        p.matchup_period,
        p.team_id,
        p.team_name,
        p.team_abbrev,
        p.owner_name,
        p.player_id,
        p.player_name,
        p.display_name
        {%- for row in stats -%}
            {%- set name = row[0] -%}
            {%- set deriv = row[1] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        null::float                as {{ lc }}
        {%- elif name in ['ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'] -%}
        null::float                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        p.{{ lc }}                 as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_weekly_player_active_performance') }} p
    inner join {{ ref('matchup_schedule') }} s
        on p.season_year = s.season_year
        and p.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

player_inactive_source as (
    -- The player inactive fact doesn't carry display_name (no scores-fact
    -- join in E1; nicknames matter only for output and inactive records
    -- don't surface as output in v1.0). Use player_name as the display
    -- so the column aligns with the other 3 sources.
    select
        'player'::varchar   as entity_grain,
        'inactive'::varchar as performance_status,
        pi.wasted_bucket,
        pi.season_year,
        pi.matchup_period,
        pi.team_id,
        pi.team_name,
        pi.team_abbrev,
        pi.owner_name,
        pi.player_id,
        pi.player_name,
        pi.player_name      as display_name
        {%- for row in stats -%}
            {%- set name = row[0] -%}
            {%- set deriv = row[1] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        null::float                as {{ lc }}
        {%- elif name in ['ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'] -%}
        null::float                as {{ lc }}
        {%- elif name in ['PLATFORM_POINTS', 'PLATFORM_HITTING_PTS', 'PLATFORM_PITCHING_PTS'] -%}
        null::float                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        pi.{{ lc }}                as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_weekly_player_inactive_performance') }} pi
    inner join {{ ref('matchup_schedule') }} s
        on pi.season_year = s.season_year
        and pi.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

combined as (
    select * from team_active_source
    union all
    select * from team_inactive_source
    union all
    select * from player_active_source
    union all
    select * from player_inactive_source
),

unpivoted as (
    select
        entity_grain,
        performance_status,
        wasted_bucket,
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
    from combined
    unpivot (stat_value for stat_name in (
        {%- for row in stats %}
            {{ row[0] | lower }}{% if not loop.last %},{% endif %}
        {%- endfor %}
    ))
),

-- Four rank dimensions: {all_time, current_season} x {most, fewest}.
-- Each computes top-10 in its direction; combined output has
-- record_scope and record_direction columns distinguishing.
--
-- Partition includes performance_status now (Phase 7 F) so active and
-- inactive rankings don't intermingle. B1's deterministic tiebreak
-- (team_id, player_id) carries through for stable golden-test output.

all_time_most as (
    select
        'all_time'::varchar as record_scope,
        'most'::varchar     as record_direction,
        u.*,
        row_number() over (
            partition by entity_grain, performance_status, stat_name
            order by stat_value desc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from unpivoted u
),

all_time_fewest as (
    select
        'all_time'::varchar as record_scope,
        'fewest'::varchar   as record_direction,
        u.*,
        row_number() over (
            partition by entity_grain, performance_status, stat_name
            order by stat_value asc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from unpivoted u
),

current_season_most as (
    select
        'current_season'::varchar as record_scope,
        'most'::varchar           as record_direction,
        u.*,
        row_number() over (
            partition by entity_grain, performance_status, stat_name
            order by stat_value desc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from unpivoted u
    where u.season_year = (select y from current_year)
),

current_season_fewest as (
    select
        'current_season'::varchar as record_scope,
        'fewest'::varchar         as record_direction,
        u.*,
        row_number() over (
            partition by entity_grain, performance_status, stat_name
            order by stat_value asc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from unpivoted u
    where u.season_year = (select y from current_year)
)

select * from all_time_most         where rank <= 10
union all
select * from all_time_fewest       where rank <= 10
union all
select * from current_season_most   where rank <= 10
union all
select * from current_season_fewest where rank <= 10
