-- assert_rivalry_matchups_have_closure_evidence.sql
-- No counted meeting comes from a season nobody has proved is over.
--
-- The tripwire for the fail-open this ledger has already had twice, once at
-- each layer. Both versions looked identical from the outside -- a rivalry
-- record that was simply a bit too long -- and neither would have failed a
-- reciprocity, grain or partition test, because a phantom result is perfectly
-- well-formed. Only asking "what is the EVIDENCE for this season" catches it.
--
-- Two admissible answers, and nothing else:
--   * the schedule capture reached this season, and this period is closed;
--   * the capture did not reach it, and the season is proven finished by
--     other means (final ranks, parsed final standings, supersession).
--
-- An uncaptured, unproven season is the failure this exists to name: the
-- latest season of a league that has never run the schedule extract, whose
-- matchups are running scores rather than results.
--
-- Restates the model's own predicates on purpose. If they drift apart this
-- stops measuring the model, so a change to the meetings CTE belongs here too.

with counted as (
    select
        m.league_key,
        m.season_year,
        m.matchup_period,
        m.team_id
    from {{ ref('mart_team_matchup') }} m
    join {{ ref('int_league_season_closure') }} sc
        on m.league_key = sc.league_key
        and m.season_year = sc.season_year
    left join {{ ref('int_matchup_period_evidence') }} pe
        on m.league_key = pe.league_key
        and m.season_year = pe.season_year
        and m.matchup_period = pe.matchup_period
    where m.opponent_id is not null
      and m.team_id <> m.opponent_id
      and m.platform_points is not null
      and m.opponent_points is not null
      and m.result is not null
      and (case when sc.has_schedule_capture then coalesce(pe.is_closed, false)
                else sc.is_season_complete end)
)

select
    c.league_key,
    c.season_year,
    c.matchup_period,
    c.team_id,
    sc.has_schedule_capture,
    sc.is_season_complete,
    sc.completion_evidence,
    pe.is_closed
from counted c
join {{ ref('int_league_season_closure') }} sc
    on c.league_key = sc.league_key
    and c.season_year = sc.season_year
left join {{ ref('int_matchup_period_evidence') }} pe
    on c.league_key = pe.league_key
    and c.season_year = pe.season_year
    and c.matchup_period = pe.matchup_period
where not (
        (sc.has_schedule_capture and coalesce(pe.is_closed, false))
        or (not sc.has_schedule_capture and sc.is_season_complete)
    )
