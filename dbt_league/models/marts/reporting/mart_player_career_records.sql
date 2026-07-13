-- mart_player_career_records.sql
-- The player CAREER record book: top-10 all-time accumulations for each
-- record-candidate stat -- the MLB-69 accumulation axis, sibling of
-- mart_player_season_records (the peak axis). Same source
-- (int_cbs__player_season_stats, the calculated_ lens over the universal
-- baseball layer), same dim_stat candidacy, same ranking conventions --
-- the Records tab renders the two side by side.
--
-- "Career" here means LEAGUE-ERA career: the sum of a player's seasons
-- within the league's own era (the source's data-driven 2001 floor), on
-- the interim total lens (production regardless of rostered/active status)
-- with the same population caveats the season book carries. The two-way
-- sentinels remain two players (900 Batter / 901 Pitcher, MLB-68); the
-- ui-only- synthetic identities are collision-free with real ids by
-- construction (one identity per mlbam), so careers never double-count.
--
-- first/last season and the season count ride along for display
-- ("2001-2013 · 13 seasons"). Rank is unique 1..10; ties break on the more
-- recent last_season (the ESPN recency convention), then player_id for
-- byte-stable output.
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

careers as (
    select
        s.league_key,
        s.stat_name,
        s.cbs_player_id             as player_id,
        max(s.cbs_player_name)      as player_name,
        count(distinct s.season_year) as seasons_played,
        min(s.season_year)          as first_season,
        max(s.season_year)          as last_season,
        sum(s.stat_value)           as stat_value
    from {{ ref('int_cbs__player_season_stats') }} s
    inner join candidates c
        on s.stat_name = c.stat_name
    group by s.league_key, s.stat_name, s.cbs_player_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by league_key, stat_name
            order by stat_value desc, last_season desc, player_id
        ) as rank
    from careers
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
    r.seasons_played,
    r.first_season,
    r.last_season,
    r.stat_value
from ranked r
inner join candidates c
    on r.stat_name = c.stat_name
where r.rank <= 10
order by r.stat_name, r.rank
