-- league_watermark.sql
-- Per-league incremental watermark for the weekly facts (MLB-57).
--
-- Pre-re-grain, each incremental fact filtered on a GLOBAL watermark:
--   (season_year * 100 + matchup_period) >= max(...) over the whole table
-- which is correct only while exactly one league exists. With league_key
-- in every grain, a league that trails the furthest-along league (a
-- mid-backfill CBS season while ESPN sits at the current week) would be
-- skipped entirely by a global max. The watermark therefore correlates
-- per league: each league re-processes from ITS OWN latest loaded
-- period, and a league with no rows yet processes everything
-- (COALESCE 0).
--
-- For a single-league warehouse this selects exactly the same rows as
-- the global form -- the ESPN goldens don't move.
--
-- NO MORE PACKING (MLB-222, findings T-01/T-09). The `* 100 +` key
-- assumed at most 99 matchup periods in a season. A league that plays
-- daily matchups has ~186, and there period 150 of 2026 packs to 202750
-- while period 1 of 2027 packs to 202701 -- so the first weeks of every
-- new season sat BELOW the watermark and were silently skipped on
-- incremental runs. The comparison is now (season_year, matchup_period)
-- as two columns, which orders correctly for any period count. Two
-- behaviours are kept on purpose and proven identical to the old form
-- for every period count up to 99 (scratch DuckDB proof, 2026-09-14):
-- an empty table admits everything, and a NULL matchup_period row is
-- excluded -- the old predicate went NULL on it, this one says so.
--
-- Usage (as the model's final WHERE, replacing the old is_incremental
-- block):    {{ league_period_watermark('alias') }}
-- where `alias` is the model's outermost row source carrying league_key,
-- season_year, and matchup_period.

{% macro league_period_watermark(alias) %}
{% if is_incremental() %}
where {{ alias }}.matchup_period is not null
  and (
      {{ alias }}.season_year > (
          select coalesce(max(w.season_year), 0)
          from {{ this }} w
          where w.league_key = {{ alias }}.league_key
      )
      or (
          {{ alias }}.season_year = (
              select coalesce(max(w.season_year), 0)
              from {{ this }} w
              where w.league_key = {{ alias }}.league_key
          )
          and {{ alias }}.matchup_period >= (
              select coalesce(max(w.matchup_period), 0)
              from {{ this }} w
              where w.league_key = {{ alias }}.league_key
                and w.season_year = (
                    select max(w2.season_year)
                    from {{ this }} w2
                    where w2.league_key = {{ alias }}.league_key
                )
          )
      )
  )
{% endif %}
{% endmacro %}
