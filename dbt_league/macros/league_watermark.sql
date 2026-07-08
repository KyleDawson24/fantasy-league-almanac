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
-- Usage (as the model's final WHERE, replacing the old is_incremental
-- block):    {{ league_period_watermark('alias') }}
-- where `alias` is the model's outermost row source carrying league_key,
-- season_year, and matchup_period.

{% macro league_period_watermark(alias) %}
{% if is_incremental() %}
where ({{ alias }}.season_year * 100 + {{ alias }}.matchup_period) >= (
    select coalesce(max(w.season_year * 100 + w.matchup_period), 0)
    from {{ this }} w
    where w.league_key = {{ alias }}.league_key
)
{% endif %}
{% endmacro %}
