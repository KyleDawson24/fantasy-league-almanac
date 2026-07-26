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
-- Grain: (league_key, entity_grain, performance_status, stat_name,
-- record_scope, record_direction, rank). entity_grain in {'team', 'player'};
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
-- Compile-time fetch of the stat universe from dim_stat. is_record_
-- candidate=true returns the leaderboard columns (40 raw counting +
-- 4 derived + 6 rate + 2 totals (wasted + negative) + 6 score = 58
-- as of v1.x). leaderboard_name is the dim's canonical column (seed
-- '1B'/'2B'/'3B' -> 'SINGLES'/'DOUBLES'/'TRIPLES', '30' -> 'CYC', '64'
-- -> 'SHO'); reading it here means the seed -> leaderboard translation
-- lives in exactly one place (dim_stat) instead of duplicated across
-- this loop, the dim, and the Python stat_catalog helpers.
--
-- run_query executes at compile time; the loop unrolls into static SQL
-- before Snowflake sees it. `execute` guard yields an empty list during
-- the parse phase so model parsing doesn't require dim_stat to exist
-- yet. dbt's DAG ordering guarantees dim_stat is built before this
-- model compiles, so the run_query at compile time has fresh data.
{% set stat_query %}
    select
        leaderboard_name,
        derivation_expr,
        qualifier_stat,
        qualifier_min
    from {{ ref('dim_stat') }}
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
    -- v1.1.0: sourced from the team-active fact instead of raw.box_scores
    -- so the DAG shows mart_stat_leaderboard depending on mart-layer
    -- contracts rather than a raw-source edge. Functionally identical;
    -- the team fact is incrementally built off the same raw rows.
    -- MLB-57: per-league -- each league's "current season" is its own
    -- latest loaded year (a mid-backfill league must not inherit another
    -- league's current season).
    select league_key, max(season_year) as y
    from {{ ref('fct_team_weekly_active_performance') }}
    group by league_key
),

team_active_source as (
    select
        t.league_key,
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
            {%- set qual_stat = row[2] -%}
            {%- set qual_min = row[3] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        null::double                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- elif qual_min -%}
        -- v1.1.1: rate-stat min-volume gate. Below the qualifier
        -- threshold, project NULL so UNPIVOT's EXCLUDE NULLS drops the
        -- row entirely. Qualifier columns come from dim_stat.
        case when t.{{ qual_stat | lower }} >= {{ qual_min }} then t.{{ lc }} else null end as {{ lc }}
        {%- else -%}
        t.{{ lc }}                 as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_team_weekly_active_performance') }} t
    -- v1.1.0: is_abnormal denormalized onto the weekly facts -- no
    -- need for the dim/seed JOIN to filter abnormal weeks.
    where t.is_abnormal = false
),

team_inactive_source as (
    select
        ti.league_key,
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
            {%- set qual_min = row[3] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        case when ti.wasted_bucket = 'ROSTERED_INACTIVE'
             then ti.calculated_points else null end as {{ lc }}
        {%- elif qual_min -%}
        -- v1.1.1: rate stats absent from inactive facts (bench rates
        -- aren't meaningful). Driven by qual_min flag in dim_stat
        -- rather than a hardcoded stat list.
        null::double                as {{ lc }}
        {%- elif name in ['PLATFORM_POINTS', 'PLATFORM_HITTING_PTS', 'PLATFORM_PITCHING_PTS'] -%}
        null::double                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        ti.{{ lc }}                as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_team_weekly_inactive_performance') }} ti
    where ti.is_abnormal = false
),

player_active_source as (
    select
        p.league_key,
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
            {%- set qual_min = row[3] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        null::double                as {{ lc }}
        {%- elif qual_min -%}
        -- v1.1.1: rate stats deliberately dropped at player grain
        -- (Phase 6.3.3 Path A: single-IP relief produces 27.00 WHIP
        -- outliers that dominate without signal). qual_min flag in
        -- dim_stat drives this, replacing the hardcoded rate-stat list.
        null::double                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        p.{{ lc }}                 as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_player_weekly_active_performance') }} p
    where p.is_abnormal = false
),

player_inactive_source as (
    -- The player inactive fact doesn't carry display_name (no scores-fact
    -- join in E1; nicknames matter only for output and inactive records
    -- don't surface as output in v1.0). Use player_name as the display
    -- so the column aligns with the other 3 sources.
    select
        pi.league_key,
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
            {%- set qual_min = row[3] -%}
            {%- set lc = name | lower %},
        {% if name == 'WASTED_POINTS' -%}
        null::double                as {{ lc }}
        {%- elif qual_min -%}
        -- v1.1.1: rate stats absent from player inactive (same Path A
        -- reasoning as player active). Driven by qual_min flag.
        null::double                as {{ lc }}
        {%- elif name in ['PLATFORM_POINTS', 'PLATFORM_HITTING_PTS', 'PLATFORM_PITCHING_PTS'] -%}
        null::double                as {{ lc }}
        {%- elif deriv -%}
        ({{ deriv }})              as {{ lc }}
        {%- else -%}
        pi.{{ lc }}                as {{ lc }}
        {%- endif -%}
        {%- endfor %}
    from {{ ref('fct_player_weekly_inactive_performance') }} pi
    where pi.is_abnormal = false
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

-- v1.2: attach owner_display via the owner_id bridge BEFORE the unpivot,
-- so every leaderboard consumer (almanac Records tab, recap records
-- section, standalone records report) gets a proper owner name from the
-- same single join.
-- v1.3: resolve it to a CANONICAL value here -- COALESCE back to the raw
-- owner_name for the defunct ownerless team (2025 team 7) -- so consumers
-- read owner_display directly without their own COALESCE.
combined_with_owner as (
    select
        c.*,
        coalesce(tod.owner_display, c.owner_name) as owner_display
    from combined c
    left join {{ ref('dim_team_owner') }} tod
        on c.league_key = tod.league_key
        and c.season_year = tod.season_year
        and c.team_id = tod.team_id
),

unpivoted as (
    select
        league_key,
        entity_grain,
        performance_status,
        wasted_bucket,
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        owner_display,
        player_id,
        player_name,
        display_name,
        -- MLB-134: pin the case rather than inherit it. UNPIVOT folds the
        -- column NAME into a value, and engines disagree on what case that
        -- name comes out in -- Snowflake emits the stored identifier
        -- (uppercase 'AB'), others emit it as written here (lowercase
        -- 'ab'). Everything downstream keys on uppercase (SEED_TO_LEADERBOARD,
        -- the dim_stat joins, records.py), so an engine default must not be
        -- what decides it. No-op on Snowflake by construction.
        upper(stat_name) as stat_name,
        stat_value
    from combined_with_owner
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
-- inactive rankings don't intermingle, and league_key (MLB-57) so each
-- league's record book ranks only its own history. B1's deterministic
-- tiebreak (team_id, player_id) carries through for stable golden-test
-- output. The current_season scopes join current_year per league.

all_time_most as (
    select
        'all_time'::varchar as record_scope,
        'most'::varchar     as record_direction,
        u.*,
        row_number() over (
            partition by u.league_key, entity_grain, performance_status, stat_name
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
            partition by u.league_key, entity_grain, performance_status, stat_name
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
            partition by u.league_key, entity_grain, performance_status, stat_name
            order by stat_value desc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from unpivoted u
    inner join current_year cy
        on u.league_key = cy.league_key
        and u.season_year = cy.y
),

current_season_fewest as (
    select
        'current_season'::varchar as record_scope,
        'fewest'::varchar         as record_direction,
        u.*,
        row_number() over (
            partition by u.league_key, entity_grain, performance_status, stat_name
            order by stat_value asc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from unpivoted u
    inner join current_year cy
        on u.league_key = cy.league_key
        and u.season_year = cy.y
)

select * from all_time_most         where rank <= 10
union all
select * from all_time_fewest       where rank <= 10
union all
select * from current_season_most   where rank <= 10
union all
select * from current_season_fewest where rank <= 10
