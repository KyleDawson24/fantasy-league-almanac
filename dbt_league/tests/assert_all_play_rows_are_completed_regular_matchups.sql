-- assert_all_play_rows_are_completed_regular_matchups.sql
-- The all-play mart is a regular-season, completed-matchup object: every row
-- must be a mart_team_matchup row (an opponent exists, so byes and the
-- FA-pool row are out) with a platform score, on a period the calendar does
-- not mark as playoff. A row failing any of these means the gate in
-- mart_team_all_play stopped holding.
--
-- Deliberately references ONLY the mart, its matchup source and the calendar
-- dim -- not int_matchup_period_evidence / int_league_season_closure. The
-- closure gate (no open periods) is proven over synthetic leagues in
-- tests/test_team_all_play.py; referencing the closure models here would let
-- dbt's indirect selection drag this test into any build that selects them
-- without building the mart (tests/test_franchise_rivalry.py does exactly
-- that against a throwaway database).
select
    ap.league_key,
    ap.season_year,
    ap.matchup_period,
    ap.team_id,
    m.opponent_id,
    m.platform_points,
    dmp.is_playoff
from {{ ref('mart_team_all_play') }} ap
left join {{ ref('mart_team_matchup') }} m
    on ap.league_key = m.league_key
    and ap.season_year = m.season_year
    and ap.matchup_period = m.matchup_period
    and ap.team_id = m.team_id
left join {{ ref('dim_matchup_period') }} dmp
    on ap.league_key = dmp.league_key
    and ap.season_year = dmp.season_year
    and ap.matchup_period = dmp.matchup_period
where m.team_id is null
   or m.opponent_id is null
   or m.platform_points is null
   or coalesce(m.is_playoff, false)
   or coalesce(dmp.is_playoff, false)
