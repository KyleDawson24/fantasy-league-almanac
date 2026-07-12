-- mart_player_season_records.sql
-- The player-season record book: top-10 single-season performances all-time
-- for each record-candidate stat, plus the marquee best-season fantasy-point
-- records. The PEAK axis of the records reframe (best single period, where a
-- period is a season for a points league); the accumulation axis (most
-- all-time) is the sibling, MLB-69.
--
-- REBUILT on the calculated_ lens (MLB-62): stats come from the universal
-- baseball layer via int_cbs__player_season_stats -- complete for every
-- crosswalked player -- not from the platform's free-agent-only season
-- archive that silently lacked Cole, Judge, Trout, and Ohtani (the first
-- record book's fatal flaw, and the reason for the MLB-70 pivot). The
-- platform_ lens does not appear here at all: its population bias makes it
-- record-ineligible, and it lives in mart_player_fpts_reconciliation as the
-- QA anchor instead.
--
-- Reuses the shared dim_stat catalog -- record candidacy, polarity, and
-- display names come straight from it, no CBS-specific stat metadata. The
-- fantasy-point records ride the CALCULATED_POINTS / CALCULATED_HITTING_PTS
-- / CALCULATED_PITCHING_PTS stat_names (already record candidates in the
-- shared seed); IRSTR is included explicitly -- the seed marks it
-- is_record_candidate=false to keep it out of the ESPN leaderboard, with
-- CBS records deliberately surfacing it here (it's a scored category in
-- this league).
--
-- Direction: "most" (value DESC) per stat. For positive stats that's the
-- leader (most HR ever); for negative stats it's the dubious record (most
-- earned runs allowed) -- both are record-worthy, and polarity rides along
-- so the consumer labels correctly. Rank is unique 1..10, ties broken by
-- recency (newer season first), matching the ESPN leaderboard convention;
-- equal stat_value flags a genuine tie to the consumer.
--
-- Scope and lens caveats (the almanac labels these):
--   - The interim TOTAL lens: production regardless of rostered/active
--     status, until MLB-63's membership reconstruction lands. Seasons span
--     the platform archive's era (2004+).
--   - Pre-2011 population is thin: CBS pruned old universes, so early-era
--     players enter the crosswalk only via career overlap with later
--     archives.
--   - Rates (AVG/ERA/WHIP...) are excluded for now -- they need the
--     min-volume qualifier handling dim_stat carries; a qualified-rate pass
--     is a follow-up. PG is a candidate but emits no rows: perfection is
--     underivable from the universal feed (see the engine's note). The
--     two-way sentinels appear as two players (900 Batter / 901 Pitcher),
--     per MLB-68.
--
-- Materialization: table (ranked record book; small).

{{ config(materialized='table') }}

with candidates as (
    select
        stat_name,
        display_name,
        leaderboard_name,
        polarity,
        stat_category
    from {{ ref('dim_stat') }}
    where (is_record_candidate and qualifier_stat is null)  -- counting records
       or stat_name = 'IRSTR'   -- CBS-scored category; ESPN-suppressed by seed flag
),

ranked as (
    select
        s.league_key,
        s.stat_name,
        s.cbs_player_id as player_id,
        s.cbs_player_name as player_name,
        s.season_year,
        s.stat_value,
        row_number() over (
            partition by s.league_key, s.stat_name
            order by s.stat_value desc, s.season_year desc
        ) as rank
    from {{ ref('int_cbs__player_season_stats') }} s
    inner join candidates c
        on s.stat_name = c.stat_name
)

select
    r.league_key,
    r.stat_name,
    c.display_name,
    c.leaderboard_name,
    c.polarity,
    c.stat_category,
    r.rank,
    r.player_id,
    r.player_name,
    r.season_year,
    r.stat_value
from ranked r
inner join candidates c
    on r.stat_name = c.stat_name
where r.rank <= 10
order by r.stat_name, r.rank
