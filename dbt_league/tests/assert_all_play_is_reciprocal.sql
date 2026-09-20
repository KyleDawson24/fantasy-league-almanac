-- assert_all_play_is_reciprocal.sql
-- Every strict all-play win is some other team's strict loss in the same
-- period, so league-wide the two totals must agree per (league, season,
-- period); ties are counted on both sides so their total is even; and every
-- team in a period was compared against exactly the other teams in it (no
-- self-comparison, nobody missing), so opponent_count is the period's team
-- count minus one and the three counts sum to it.
with periods as (
    select
        league_key,
        season_year,
        matchup_period,
        count(*)                       as team_rows,
        sum(all_play_wins)             as wins,
        sum(all_play_losses)           as losses,
        sum(all_play_ties)             as ties,
        min(opponent_count)            as min_opponents,
        max(opponent_count)            as max_opponents,
        sum(case when all_play_wins + all_play_losses + all_play_ties
                      <> opponent_count then 1 else 0 end) as unbalanced_rows
    from {{ ref('mart_team_all_play') }}
    group by 1, 2, 3
)

select *
from periods
where wins <> losses
   or ties % 2 <> 0
   or min_opponents <> team_rows - 1
   or max_opponents <> team_rows - 1
   or unbalanced_rows <> 0
