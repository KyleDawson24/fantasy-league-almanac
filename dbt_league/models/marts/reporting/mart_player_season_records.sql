-- mart_player_season_records.sql
-- The player-season record book: top-10 single-season performances all-time
-- for each record-candidate stat, plus the marquee "best season" by the
-- platform's own points. The PEAK axis of the records reframe (best single
-- period, where a period is a season for a points league); the accumulation
-- axis (most all-time) is the sibling, MLB-69.
--
-- Reuses the shared dim_stat catalog -- the whole point of the vocabulary
-- bridge: CBS's stats arrive here already in ESPN stat_name form, so record
-- candidacy, polarity, and display names come straight from dim_stat, no
-- CBS-specific stat metadata. Platform-neutral by shape; fed by CBS today
-- (stg_cbs__player_season_stats), any league with a player-season-stats
-- staging later.
--
-- Direction: "most" (value DESC) per stat. For positive stats that's the
-- leader (most HR ever); for negative stats it's the dubious record (most
-- earned runs allowed) -- both are record-worthy, and polarity rides along so
-- the consumer labels correctly. Rank is unique 1..10, ties broken by recency
-- (newer season first), matching the ESPN leaderboard convention; equal
-- stat_value flags a genuine tie to the consumer.
--
-- Scope: rates (AVG/ERA/WHIP...) are excluded for now -- they need the
-- min-volume qualifier handling dim_stat carries (qualifier_stat/min); a
-- qualified-rate pass is a follow-up. PLATFORM_POINTS is included explicitly
-- (the season-FPTS record) even though it's is_record_candidate=false in the
-- ESPN seed. The two-way sentinels (900/901) are absent -- season universes
-- omit them; their records ride the gamelog path.
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
       or stat_name = 'PLATFORM_POINTS'                     -- the season-FPTS record
),

ranked as (
    select
        s.league_key,
        s.stat_name,
        s.player_id,
        s.player_name,
        s.season_year,
        s.universe,
        s.stat_value,
        row_number() over (
            partition by s.league_key, s.stat_name
            order by s.stat_value desc, s.season_year desc
        ) as rank
    from {{ ref('stg_cbs__player_season_stats') }} s
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
    r.universe,
    r.stat_value
from ranked r
inner join candidates c
    on r.stat_name = c.stat_name
where r.rank <= 10
order by r.stat_name, r.rank
