-- mart_team_position_eligibility.sql
-- How many players on a team's CURRENT roster are eligible at each
-- position (MLB-265). The Advanced Standings eligibility grid reads this.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id, lineup_slot).
-- ==========================================================================
--
-- A PLAYER COUNTS AT EVERY POSITION HE IS ELIGIBLE FOR, so the row total
-- across a team EXCEEDS its roster size, by design and not by double
-- counting: a 2B/SS adds one to 2B and one to SS because he really can be
-- deployed at either. Reading a row as "players available here" is correct;
-- reading the sum as "players rostered" is not.
--
-- CURRENT SNAPSHOT ONLY. There is no history and no as-of parameter: the
-- question is "what does this roster look like right now", and eligibility
-- that has since lapsed or been earned would answer a different one. Each
-- platform's own latest capture defines "now" (they do not land in step),
-- which is why snapshot_key rides on every row rather than being assumed
-- by the consumer.
--
-- THE ELIGIBILITY QUESTION IS NOT THE DEPLOYMENT QUESTION. Its sibling
-- mart_team_slot_production counts points produced while DEPLOYED in a
-- slot -- what a manager did. This counts who COULD be deployed there --
-- what a manager may do. The Advanced Standings tab shows both grids, and
-- they will disagree constantly; that disagreement is the interesting part.
--
-- ==========================================================================
-- WHERE EACH PLATFORM'S ELIGIBILITY COMES FROM
-- ==========================================================================
--
-- ESPN -- mart_daily_roster_snapshot, NOT fct_player_daily_performance.
--   The performance fact starts from stat breakdowns, so a rostered player
--   with no stats that day does not survive it; counting a roster from it
--   would quietly omit every off-day pitcher and quiet bat. The roster
--   snapshot is the model that exists precisely to keep them, and it
--   already excludes the FA pool (a free agent is on no team's roster).
--
-- CBS -- the CAPTURED eligibility list (stg_cbs__rosters.eligible_positions,
--   CBS's own served string), falling back to the DERIVED windows
--   (int_cbs__eligibility_windows) for any player the capture did not carry
--   one for. Kyle's call, 2026-08-31.
--
--   The fallback is currently a NO-OP and that is worth stating rather than
--   discovering: on the 2026-08-30 capture all 480 rostered players carry a
--   served list, so 0 rows resolve through it. It is built anyway because
--   the failure it covers is a capture gap, which is exactly the case that
--   arrives unannounced.
--
--   The two sources are NOT equivalent and the row says which one answered.
--   Captured is what CBS served. Derived is this project's reconstruction of
--   the league's own rule ("primary position, plus 20 games last year or 10
--   this year"), whose primary-position term is an ESTIMATOR graded ~93%
--   against these very captures. n_captured / n_derived carry the split as
--   COUNTS rather than a label, so a mixed cell stays readable and no string
--   aggregation has to agree across two engines.
--
-- ==========================================================================
-- WHICH SLOTS BECOME COLUMNS
-- ==========================================================================
--
-- dim_lineup_slot.is_position_slot decides, so UTIL / BE / IL / FA / RS /
-- ACT / EST drop out by their seeded classification and never by a literal
-- list here. ESPN is additionally scoped to the slots THIS league-season
-- actually configured (dim_roster_slot_counts, read from rosterSettings) --
-- the league's own settings, not an assumption about what ESPN offers. CBS
-- serves no roster-settings feed, so its seeded position vocabulary rides
-- whole; that asymmetry is the data's, not a shortcut.
--
-- Materialization: table. Small, and it feeds a byte-diff golden -- the same
-- determinism rationale as mart_team_slot_production.
{{ config(materialized='table') }}

with slots as (
    select
        platform,
        lineup_slot,
        canonical_slot_key,
        sort_order
    from {{ ref('dim_lineup_slot') }}
    where is_position_slot
),

-- ESPN ---------------------------------------------------------------------

-- "Current" is ONE snapshot per league, not one per season. Taking the
-- latest scoring_period WITHIN each season_year looks equivalent and is
-- not: it emits a current roster for every season the warehouse holds, so
-- espn-main rendered 2026's 14 teams AND a prior season's 16. That is
-- history, which this mart does not do. The latest SEASON is chosen first
-- and the latest period is read inside it.
espn_current_season as (
    select
        league_key,
        max(season_year) as season_year
    from {{ ref('mart_daily_roster_snapshot') }}
    group by 1
),

espn_latest as (
    select
        s.league_key,
        s.season_year,
        max(s.scoring_period) as scoring_period
    from {{ ref('mart_daily_roster_snapshot') }} s
    join espn_current_season c
        on  c.league_key  = s.league_key
        and c.season_year = s.season_year
    group by 1, 2
),

espn_roster as (
    select
        s.league_key,
        s.season_year,
        s.scoring_period,
        s.team_id,
        s.team_abbrev,
        s.player_id,
        s.eligible_slots
    from {{ ref('mart_daily_roster_snapshot') }} s
    join espn_latest l
        on  l.league_key    = s.league_key
        and l.season_year   = s.season_year
        and l.scoring_period = s.scoring_period
),

espn_exploded as (
    select
        r.league_key,
        'espn'                                          as platform,
        r.season_year,
        cast(r.team_id as {{ dbt.type_string() }})      as team_id,
        r.team_abbrev,
        cast(r.scoring_period as {{ dbt.type_string() }}) as snapshot_key,
        cast(r.player_id as {{ dbt.type_string() }})    as player_key,
        {{ json_unwrap_text('slot.value') }}            as lineup_slot,
        'captured'                                      as eligibility_source
    from espn_roster r,
        {{ flatten_array('r.eligible_slots', 'slot') }}
),

-- The league's own configured slots. Restricting here rather than in the
-- consumer means a league that does not field a slot never renders a
-- column of zeroes for it.
espn_configured as (
    select distinct
        league_key,
        season_year,
        lineup_slot
    from {{ ref('dim_roster_slot_counts') }}
),

espn_final as (
    select e.*
    from espn_exploded e
    join espn_configured c
        on  c.league_key  = e.league_key
        and c.season_year = e.season_year
        and c.lineup_slot = e.lineup_slot
),

-- CBS ----------------------------------------------------------------------

-- Same one-snapshot-per-league rule as the ESPN arm above.
cbs_current_season as (
    select
        league_key,
        max(season_year) as season_year
    from {{ ref('stg_cbs__rosters') }}
    group by 1
),

cbs_latest as (
    select
        r.league_key,
        r.season_year,
        max(r.roster_date) as roster_date
    from {{ ref('stg_cbs__rosters') }} r
    join cbs_current_season c
        on  c.league_key  = r.league_key
        and c.season_year = r.season_year
    group by 1, 2
),

cbs_roster as (
    select
        r.league_key,
        r.season_year,
        r.roster_date,
        r.team_id,
        r.team_abbr,
        r.player_id,
        r.eligible_positions
    from {{ ref('stg_cbs__rosters') }} r
    join cbs_latest l
        on  l.league_key  = r.league_key
        and l.season_year = r.season_year
        and l.roster_date = r.roster_date
),

-- Served list. The empty-string element the split macro deliberately does
-- not swallow is filtered here (see split_to_rows' contract).
cbs_captured as (
    select
        r.league_key,
        'cbs'                                            as platform,
        r.season_year,
        cast(r.team_id as {{ dbt.type_string() }})       as team_id,
        r.team_abbr                                      as team_abbrev,
        cast(r.roster_date as {{ dbt.type_string() }})   as snapshot_key,
        cast(r.player_id as {{ dbt.type_string() }})     as player_key,
        trim({{ json_unwrap_text('pos.value') }})        as lineup_slot,
        'captured'                                       as eligibility_source
    from cbs_roster r,
        {{ split_to_rows('r.eligible_positions', ',', 'pos') }}
    where r.eligible_positions is not null
      and trim(coalesce({{ json_unwrap_text('pos.value') }}, '')) <> ''
),

-- Fallback population: rostered, but the capture served no list at all.
cbs_uncaptured as (
    select *
    from cbs_roster
    where eligible_positions is null
       or trim(eligible_positions) = ''
),

cbs_derived as (
    select
        u.league_key,
        'cbs'                                            as platform,
        u.season_year,
        cast(u.team_id as {{ dbt.type_string() }})       as team_id,
        u.team_abbr                                      as team_abbrev,
        cast(u.roster_date as {{ dbt.type_string() }})   as snapshot_key,
        cast(u.player_id as {{ dbt.type_string() }})     as player_key,
        w.cbs_position                                   as lineup_slot,
        'derived'                                        as eligibility_source
    from cbs_uncaptured u
    join {{ ref('stg_cbs__mlbam_crosswalk') }} x
        on x.cbs_player_id = u.player_id
    join {{ ref('int_cbs__eligibility_windows') }} w
        on  w.league_key  = u.league_key
        and w.season_year = u.season_year
        and w.mlbam_id    = x.mlbam_id
    -- Only windows already OPEN on the snapshot date. A window that opens
    -- next week is not eligibility the manager has today.
    where w.eligible_from <= u.roster_date
),

-- Union --------------------------------------------------------------------

unioned as (
    select * from espn_final
    union all
    select * from cbs_captured
    union all
    select * from cbs_derived
)

select
    u.league_key,
    u.platform,
    u.season_year,
    u.team_id,
    u.team_abbrev,
    u.snapshot_key,
    u.lineup_slot,
    s.canonical_slot_key,
    s.sort_order,
    count(distinct u.player_key)                                     as eligible_player_count,
    count(distinct case when u.eligibility_source = 'captured'
                        then u.player_key end)                       as n_captured,
    count(distinct case when u.eligibility_source = 'derived'
                        then u.player_key end)                       as n_derived
from unioned u
join slots s
    on  s.platform    = u.platform
    and s.lineup_slot = u.lineup_slot
group by
    u.league_key, u.platform, u.season_year, u.team_id, u.team_abbrev,
    u.snapshot_key, u.lineup_slot, s.canonical_slot_key, s.sort_order
order by
    u.league_key, u.season_year, u.team_id, s.sort_order, u.lineup_slot
