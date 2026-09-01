-- Singular test: every ESPN row's derived game_date must equal the season
-- opener plus its scoring_period ordinal (MLB-263, ledger S-36).
--
-- ESPN serves no ISO date -- its day identity is the scoring_period ordinal
-- -- so int_player_daily derives the calendar from MLB's published
-- regular-season start. Before this derivation existed, espn_points_data
-- rebuilt the same rule in Python (season_context / period_to_date /
-- month_window). This test is what keeps the ONE spine honest: if the
-- anchor, the join grain or the arithmetic drifts, the renderers' month
-- windows silently slide and nothing else would say so.
--
-- The rule is the model's own, restated independently here rather than
-- re-referenced, so a change to the model cannot make the test agree with
-- it by construction.
--
-- A NULL opener is not a failure: a season with no captured calendar row
-- yields a NULL game_date by design (the join is LEFT), and the not_null
-- expectation for the seasons we DO carry is asserted by the
-- espn_game_date_is_never_null test alongside this one.
--
-- Returns one row per violating (season, period); zero rows = pass.

select
    d.season_year,
    d.scoring_period,
    min(d.game_date)                                              as got,
    min({{ to_date_of(date_add_unit('day', 'cast(d.scoring_period as integer) - 1',
                                    'c.season_opener')) }})       as expected,
    count(*)                                                      as bad_rows
from {{ ref('fct_player_daily_performance') }} d
join {{ ref('stg_mlb__season_calendar') }} c
    on d.season_year = c.season_year
where d.league_key like 'espn%'
  and c.season_opener is not null
  and d.game_date
      is distinct from {{ to_date_of(date_add_unit('day', 'cast(d.scoring_period as integer) - 1',
                                                   'c.season_opener')) }}
group by 1, 2
