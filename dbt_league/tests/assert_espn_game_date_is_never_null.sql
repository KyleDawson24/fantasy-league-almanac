-- Singular test: no ESPN row may carry a NULL game_date (MLB-263, S-36).
--
-- The daily contract now has ONE date vocabulary -- CBS serves the date,
-- ESPN derives it -- and a NULL here would mean an ESPN season slipped
-- through with no calendar anchor, quietly handing every date-windowed
-- surface an unfiltered or empty window instead of an error.
--
-- Scoped to seasons the calendar actually covers, because "we have not
-- captured that season's opener yet" is a real and different state from
-- "the derivation broke".
--
-- Returns one row per offending season; zero rows = pass.

select
    d.league_key,
    d.season_year,
    count(*) as null_game_date_rows
from {{ ref('fct_player_daily_performance') }} d
join {{ ref('stg_mlb__season_calendar') }} c
    on d.season_year = c.season_year
where d.league_key like 'espn%'
  and c.season_opener is not null
  and d.game_date is null
group by 1, 2
