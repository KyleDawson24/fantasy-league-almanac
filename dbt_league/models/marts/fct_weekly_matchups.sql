-- int_weekly_matchups.sql
-- One row per team per matchup period with opponent info and win/loss.
-- Enables "tough luck" and "lucky bastard" callouts.

with daily as (
    select * from {{ ref('stg_box_scores') }}
    where lineup_slot not in ('BE', 'IL')
),

-- Get matchup pairings from the raw source (home vs away)
matchup_pairs as (
    select distinct
        season_year,
        scoring_period,
        matchup_period,
        m.value:home_team::string       as home_team,
        m.value:home_team_id::integer   as home_team_id,
        m.value:away_team::string       as away_team,
        m.value:away_team_id::integer   as away_team_id
    from {{ source('raw', 'box_scores') }},
        lateral flatten(input => raw_json) m
    qualify row_number() over (
        partition by season_year,
            matchup_period,
            m.value:home_team_id::integer,
            m.value:away_team_id::integer
        order by scoring_period
    ) = 1
),

weekly_scores as (
    select * from {{ ref('fct_weekly_team_scores') }}
),

-- Join each team to its opponent's score
home_side as (
    select
        mp.matchup_period,
        mp.season_year,
        mp.home_team        as team_name,
        mp.home_team_id     as team_id,
        mp.away_team        as opponent_name,
        mp.away_team_id     as opponent_id,
        ws.total_points     as team_points,
        opp.total_points    as opponent_points
    from matchup_pairs mp
    inner join weekly_scores ws
        on mp.season_year = ws.season_year
        and mp.matchup_period = ws.matchup_period
        and mp.home_team_id = ws.team_id
    inner join weekly_scores opp
        on mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.away_team_id = opp.team_id
),

away_side as (
    select
        mp.season_year,
        mp.matchup_period,
        mp.away_team        as team_name,
        mp.away_team_id     as team_id,
        mp.home_team        as opponent_name,
        mp.home_team_id     as opponent_id,
        ws.total_points     as team_points,
        opp.total_points    as opponent_points
    from matchup_pairs mp
    inner join weekly_scores ws
        on mp.season_year = ws.season_year
        and mp.matchup_period = ws.matchup_period
        and mp.away_team_id = ws.team_id
    inner join weekly_scores opp
        on mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.home_team_id = opp.team_id
),

combined as (
    select *, case when team_points > opponent_points then 'W' else 'L' end as result
    from home_side
    union all
    select *, case when team_points > opponent_points then 'W' else 'L' end as result
    from away_side
)

select * from combined