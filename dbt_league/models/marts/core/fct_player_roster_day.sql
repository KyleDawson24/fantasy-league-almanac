-- fct_player_roster_day.sql
-- Every day a rosterable asset sat on a fantasy roster, game or no game
-- (master-table contract v0.3, ruling R5 / warehouse ask W10; MLB-319).
--
-- WHY IT EXISTS. The daily performance fact only has rows for days with an
-- MLB game, so "days rostered", tenure and "my roster on an off day" cannot
-- be counted from it. The dashboard's roster-day counts need the dense
-- version. This model is that roster ledger in one shape for every league.
--
-- TWO SOURCES, ONE SHAPE, NO PLATFORM CONDITION:
--   * mart_daily_roster_snapshot -- a platform's own daily roster capture,
--     where one exists (ESPN today). Its day key is the platform's ordinal,
--     mapped to a date through the MLB season opener exactly as the
--     published dashboard already does (and as the daily fact validates:
--     game_date = opener + scoring_period - 1 on every ESPN row).
--   * where no snapshot exists (CBS today), three layers, the most direct
--     evidence winning any day they both claim:
--       1. observed_game_day -- the daily fact's own rostered rows (the
--          asset had a game and a team credited it);
--       2. inferred_gap -- the days BETWEEN two game days on the SAME team
--          on which the asset has no row at all (off days, the All-Star
--          break, an injured spell). An asset with a game that day and no
--          rostered row was not rostered, so only row-less days are filled;
--       3. reconstructed_stint -- add/drop stints from the transaction
--          record expanded to days, for tenure the first two cannot see.
--          Stints whose asset never resolved to a key are left out, never
--          guessed (8,615 of 20,077 at MLB-319).
-- A roster day carries the deployed slot only where the source observed one.
-- Eligibility on a no-game day is carried forward from the asset's most
-- recent game day in that season (NULL before its first).
--
-- GRAIN: one row per (league_key, roster_date, player_key).

{{ config(materialized='table') }}

with snapshot_days as (
    select
        r.league_key,
        r.season_year,
        cast({{ dbt.dateadd('day', 'cast(r.scoring_period as integer) - 1', 'c.season_opener') }} as date) as roster_date,
        cast(r.player_id as {{ dbt.type_string() }}) as player_key,
        r.team_id,
        r.lineup_slot,
        r.eligible_slots,
        r.roster_status,
        'platform_roster_snapshot' as roster_source
    from {{ ref('mart_daily_roster_snapshot') }} r
    inner join {{ ref('stg_mlb__season_calendar') }} c
        on c.season_year = r.season_year
),

snapshot_leagues as (
    select distinct league_key from snapshot_days
),

observed as (
    select
        f.league_key,
        f.season_year,
        f.game_date as roster_date,
        f.player_key,
        f.team_id,
        f.lineup_slot,
        f.eligible_slots,
        1 as evidence_rank,
        'observed_game_day' as roster_source
    from {{ ref('fct_player_daily_performance') }} f
    where f.team_id is not null
      and f.league_key not in (select league_key from snapshot_leagues)
),

observed_next as (
    select
        o.*,
        lead(o.roster_date) over (
            partition by o.league_key, o.season_year, o.player_key order by o.roster_date
        ) as next_date,
        lead(o.team_id) over (
            partition by o.league_key, o.season_year, o.player_key order by o.roster_date
        ) as next_team_id
    from observed o
),

calendar as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2001-01-01' as date)",
        end_date="cast('2027-01-01' as date)"
    ) }}
),

any_row_days as (
    select distinct league_key, game_date, player_key
    from {{ ref('fct_player_daily_performance') }}
),

gaps as (
    select
        o.league_key,
        o.season_year,
        cast(c.date_day as date) as roster_date,
        o.player_key,
        o.team_id,
        cast(null as {{ dbt.type_string() }}) as lineup_slot,
        null as eligible_slots,
        2 as evidence_rank,
        'inferred_gap' as roster_source
    from observed_next o
    inner join calendar c
        on cast(c.date_day as date) > o.roster_date
        and cast(c.date_day as date) < o.next_date
    left join any_row_days a
        on a.league_key = o.league_key
        and a.game_date = cast(c.date_day as date)
        and a.player_key = o.player_key
    where o.next_team_id = o.team_id
      and a.player_key is null
),

season_bounds as (
    select league_key, season_year, max(game_date) as last_day
    from {{ ref('fct_player_daily_performance') }}
    group by 1, 2
),

stints as (
    select
        s.league_key,
        s.season_year,
        s.resolved_cbs_player_id as player_key,
        s.franchise_id as team_id,
        s.stint_start,
        least(coalesce(s.stint_end, b.last_day), b.last_day) as stint_last
    from {{ ref('int_cbs__roster_stints') }} s
    inner join season_bounds b
        on b.league_key = s.league_key
        and b.season_year = s.season_year
    where s.resolved_cbs_player_id is not null
      and s.league_key not in (select league_key from snapshot_leagues)
),

stint_days as (
    select
        s.league_key,
        s.season_year,
        cast(c.date_day as date) as roster_date,
        s.player_key,
        s.team_id,
        cast(null as {{ dbt.type_string() }}) as lineup_slot,
        null as eligible_slots,
        3 as evidence_rank,
        'reconstructed_stint' as roster_source
    from stints s
    inner join calendar c
        on cast(c.date_day as date) between s.stint_start and s.stint_last
),

layered as (
    select
        *,
        row_number() over (
            partition by league_key, player_key, roster_date
            order by evidence_rank, team_id
        ) as pick
    from (
        select * from observed
        union all
        select * from gaps
        union all
        select * from stint_days
    ) u
),

layer_rows as (
    select
        league_key,
        season_year,
        roster_date,
        player_key,
        team_id,
        lineup_slot,
        {{ ignore_nulls('last_value', 'eligible_slots') }} over (
            partition by league_key, season_year, player_key
            order by roster_date
            rows between unbounded preceding and current row
        ) as eligible_slots,
        case when evidence_rank = 1 then 'rostered_game_day' else 'rostered' end as roster_status,
        roster_source
    from layered
    where pick = 1
)

select * from snapshot_days
union all
select * from layer_rows
