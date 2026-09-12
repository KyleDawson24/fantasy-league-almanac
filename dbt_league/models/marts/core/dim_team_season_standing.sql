{{ config(materialized='view') }}

-- dim_team_season_standing.sql
-- A team's season finish, both platforms, with the LENS named (MLB-263,
-- ledger S-31).
--
-- ESPN rows are the platform's own final standings (stg_team_standings,
-- with division and playoff-cut context): lens = 'platform', and the
-- original column set is untouched -- every column below `waiver_rank` is
-- additive. CBS rows are the league's AWARDED finishes as the UI standings
-- page shows them (stg_cbs__ui_standings): lens = 'awarded'. Each lens
-- carries its own standings_rank and is_champion, so a consumer that asks
-- "who finished where" gets one answer shape from either platform and can
-- see which platform's notion of finish it is reading. The CBS-only
-- measures (batting / pitching / total points, points behind) ride along
-- NULL on ESPN rows; ESPN's H2H columns ride along NULL on CBS rows.
--
-- A platform-lens row for a season the platform has not closed carries a
-- NULL standings_rank and NULL is_champion: ESPN serves no final rank until
-- the season ends, and no finish is invented for it (the live season is 14
-- such rows when this landed).
--
-- The ESPN champion here is the PLATFORM's final rank = 1 -- the awarded
-- lens for ESPN, matching the CBS side -- not the almanac's own recomputed
-- ordering (get_espn_season_finishes ranks by wins/ties/points itself; that
-- reader is ledger S-31's, not this model's).
--
-- Materialization: view over two staging feeds.

with platform_rows as (
    select
        s.league_key,
        s.season_year,
        s.team_id,
        s.team_abbrev,
        s.team_name,
        s.division_id,
        d.division_name,
        d.division_size,
        s.playoff_seed,
        s.final_rank,
        sc.playoff_team_count,
        case
            when s.playoff_seed is null then null
            else s.playoff_seed <= sc.playoff_team_count
        end                                             as made_playoffs,
        s.wins,
        s.losses,
        s.ties,
        s.win_percentage,
        s.games_back,
        s.streak_length,
        s.streak_type,
        s.division_wins,
        s.division_losses,
        s.division_ties,
        s.platform_points,
        s.platform_points_adjusted,
        s.waiver_rank,
        -- additive, the shared finish contract
        'platform'                                      as lens,
        cast(s.final_rank as integer)                   as standings_rank,
        (s.final_rank = 1)                              as is_champion,
        cast(null as double)                            as batting_points,
        cast(null as double)                            as pitching_points,
        cast(null as double)                            as total_points,
        cast(null as double)                            as points_behind,
        count(*) over (partition by s.league_key, s.season_year) as teams_in_season
    from {{ ref('stg_team_standings') }} s
    left join {{ ref('stg_divisions') }} d
        on  s.league_key  = d.league_key
        and s.season_year = d.season_year
        and s.division_id = d.division_id
    left join {{ ref('stg_schedule_settings') }} sc
        on  s.league_key  = sc.league_key
        and s.season_year = sc.season_year
),

awarded_rows as (
    select
        u.league_key,
        u.season_year,
        cast(u.franchise_id as integer)                 as team_id,
        cast(null as varchar)                           as team_abbrev,
        u.team_name,
        cast(null as integer)                           as division_id,
        u.division_name,
        cast(null as integer)                           as division_size,
        cast(null as integer)                           as playoff_seed,
        cast(null as integer)                           as final_rank,
        cast(null as integer)                           as playoff_team_count,
        cast(null as boolean)                           as made_playoffs,
        cast(null as integer)                           as wins,
        cast(null as integer)                           as losses,
        cast(null as integer)                           as ties,
        cast(null as double)                            as win_percentage,
        cast(null as double)                            as games_back,
        cast(null as integer)                           as streak_length,
        cast(null as varchar)                           as streak_type,
        cast(null as integer)                           as division_wins,
        cast(null as integer)                           as division_losses,
        cast(null as integer)                           as division_ties,
        cast(null as double)                            as platform_points,
        cast(null as double)                            as platform_points_adjusted,
        cast(null as integer)                           as waiver_rank,
        'awarded'                                       as lens,
        cast(u.standings_rank as integer)               as standings_rank,
        u.is_champion,
        u.batting_points,
        u.pitching_points,
        u.total_points,
        u.points_behind,
        cast(u.teams_in_season as integer)              as teams_in_season
    from {{ ref('stg_cbs__ui_standings') }} u
)

select * from platform_rows
union all
select * from awarded_rows
