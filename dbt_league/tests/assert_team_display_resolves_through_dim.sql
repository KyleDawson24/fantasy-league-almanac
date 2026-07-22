-- MLB-113 guard: every rostered row's team display must come from the franchise
-- dim, not from the platform name the fact inherited out of RAW.
--
-- fct_player_daily_performance coalesces to the platform name when the season
-- dim has no row for a (league, franchise, season). That net keeps an
-- unresolved franchise readable instead of blanking it, but if it ever starts
-- carrying rows the symptom is invisible -- names still render, they are just
-- the un-overridable RAW ones again, which is precisely the bug this ticket
-- closed. Anonymized renders would silently leak once more.
--
-- Fails on any rostered franchise-season the dim cannot resolve. Two exclusions,
-- both by construction rather than convenience:
--   - free agents (team_id NULL) miss the join and carry NULL names on purpose;
--   - the holding pen is synthetic, so it appears in no league's observed-season
--     source and can never be in the season spine. Its label already comes from
--     the holding_pen_* vars where the production is parked, so the coalesce
--     reproduces exactly what the dim would have said.
select
    p.league_key,
    p.season_year,
    p.team_id,
    count(*) as unresolved_rows
from {{ ref('fct_player_daily_performance') }} p
left join {{ ref('dim_franchise_season') }} d
    on p.league_key = d.league_key
    and cast(p.team_id as varchar) = d.franchise_id
    and p.season_year = d.season_year
where p.team_id is not null
  and cast(p.team_id as varchar) != '{{ var("holding_pen_franchise_id") }}'
  and d.franchise_id is null
group by p.league_key, p.season_year, p.team_id
