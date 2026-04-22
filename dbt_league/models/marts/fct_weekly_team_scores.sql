-- fct_weekly_team_scores.sql
-- One row per team per matchup period.
-- Combines weekly scoring totals with matchup context (opponent, W/L).
-- Sources exclusively from intermediate layer — no cross-layer dependencies.

with daily as (
    select * from {{ ref('int_team_daily_scores') }}
),

-- Weekly team score rollup
weekly_scores as (
    select
        season_year,
        matchup_period,
        owner_name,
        team_name,
        team_id,
        sum(total_points)              as total_points,
        sum(hitting_points)            as hitting_points,
        sum(pitching_points)           as pitching_points,
        count(distinct scoring_period) as days_in_period
    from daily
    group by 1, 2, 3, 4, 5
),

-- Matchup pairings from raw source (home vs away)
matchup_pairs as (
    select distinct
        season_year,
        matchup_period,
        m.value:home_team_id::integer   as home_team_id,
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

-- Join each team to its opponent via matchup pairings
with_opponents as (
    select
        ws.season_year,
        ws.matchup_period,
        ws.owner_name,
        ws.team_name,
        ws.team_id,
        ws.total_points,
        ws.hitting_points,
        ws.pitching_points,
        ws.days_in_period,
        opp.owner_name      as opponent_owner,
        opp.team_name       as opponent_name,
        opp.team_id         as opponent_id,
        opp.total_points    as opponent_points,
        case
            when ws.total_points > opp.total_points then 'W'
            when ws.total_points < opp.total_points then 'L'
            else 'T'
        end as result
    from weekly_scores ws
    -- Home side: this team is home, opponent is away
    inner join matchup_pairs mp
        on ws.season_year = mp.season_year
        and ws.matchup_period = mp.matchup_period
        and ws.team_id = mp.home_team_id
    inner join weekly_scores opp
        on mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.away_team_id = opp.team_id

    union all

    select
        ws.season_year,
        ws.matchup_period,
        ws.owner_name,
        ws.team_name,
        ws.team_id,
        ws.total_points,
        ws.hitting_points,
        ws.pitching_points,
        ws.days_in_period,
        opp.owner_name      as opponent_owner,
        opp.team_name       as opponent_name,
        opp.team_id         as opponent_id,
        opp.total_points    as opponent_points,
        case
            when ws.total_points > opp.total_points then 'W'
            when ws.total_points < opp.total_points then 'L'
            else 'T'
        end as result
    from weekly_scores ws
    -- Away side: this team is away, opponent is home
    inner join matchup_pairs mp
        on ws.season_year = mp.season_year
        and ws.matchup_period = mp.matchup_period
        and ws.team_id = mp.away_team_id
    inner join weekly_scores opp
        on mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.home_team_id = opp.team_id
)

select * from with_opponents