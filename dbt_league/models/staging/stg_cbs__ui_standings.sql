-- stg_cbs__ui_standings.sql
-- Season finishes for CBS from the parsed UI history (MLB-53): one row per
-- (league_key, season_year, franchise_id) -- a franchise's FINAL standing.
-- The completed-seasons half of F7 (the API's period standings carry the
-- live season; these carry 2001-2025), and three load-bearing byproducts:
--   - THE CHAMPIONS LIST (is_champion = rank 1) -- the first official one
--     in league history.
--   - The per-season (team_name -> franchise_id) map every other UI family
--     resolves against. Names DRIFT ('Junk Drawer All-Stars' 2008 vs 'Hot Dog
--     Junkies' now) and names get REUSED across franchises (Foster's Folly
--     is id 13 through 2019 and id 30 from 2021), so the map is
--     per-season BY DESIGN; never join names across seasons.
--   - The league-shape timeline (team count by year: 16 but 15 in 2002,
--     12 in 2020).

select
    league_key,
    season_year,
    franchise_id,
    team_name,
    division_name,
    standings_rank,
    (standings_rank = 1)  as is_champion,
    batting_points,
    pitching_points,
    total_points,
    points_behind,
    count(*) over (partition by league_key, season_year) as teams_in_season
from {{ source('raw', 'cbs_ui_standings') }}
