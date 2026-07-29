-- Payload-constancy assertion for the label-picking windows in
-- fct_player_season_performance (MLB-134, Class C).
--
-- Those two windows read team / player DISPLAY labels off a player-grain
-- fact by taking the latest matchup_period. Every other player on the same
-- team in that period is a tied row -- 30 such groups today for the team
-- labels, 262 for the player labels -- so the pick is correct only because
-- the labels are constant across the tie.
--
-- Nothing enforced that. A mid-week owner change, a rename landing on one
-- slot row, or a slot-fact anomaly would have made the emitted label
-- engine-decided: silently correct on Snowflake, silently different on
-- another engine, and invisible to the byte-diff goldens either way. The
-- order-by pins in the model make the ROW choice deterministic; this test
-- is what makes the VALUE trustworthy, which is the half a tie-breaker
-- cannot give you.
--
-- Fails (returns rows) if any team-week carries more than one distinct
-- label triple, or any player-week more than one distinct name pair.

with team_labels as (
    select
        league_key,
        season_year,
        matchup_period,
        cast(team_id as varchar) as entity_id
    from {{ ref('fct_player_weekly_slot_performance') }}
    where team_id is not null
    group by 1, 2, 3, 4
    having count(distinct coalesce(team_name, '~')
                          || '|' || coalesce(team_abbrev, '~')
                          || '|' || coalesce(owner_name, '~')) > 1
),

player_labels as (
    select
        league_key,
        season_year,
        matchup_period,
        cast(player_id as varchar) as entity_id
    from {{ ref('fct_player_weekly_slot_performance') }}
    group by 1, 2, 3, 4
    having count(distinct coalesce(player_name, '~')
                          || '|' || coalesce(display_name, '~')) > 1
)

select 'team_labels' as scope, * from team_labels
union all
select 'player_labels' as scope, * from player_labels
