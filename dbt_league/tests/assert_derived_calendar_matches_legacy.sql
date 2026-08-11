-- MLB-235 rung 4B-2: the derived calendar and the hand-maintained one must
-- not disagree.
--
-- dim_matchup_period resolves start_date/end_date derived-first, so a
-- divergence between the two sources would SILENTLY MOVE the pioneer league's
-- calendar -- and a calendar that shifts is not a visible failure, it is a
-- season of dates that are all internally consistent and one day wrong.
--
-- Kyle's ruling on this rung was explicit: if the automatic anchor and the
-- existing calendar disagree, stop and report the exact seasons and dates
-- rather than silently choosing one. This test is that stop.
--
-- IT IS EXPECTED TO RETURN NOTHING, and does today: restricted to CLOSED
-- periods -- the only ones that get derived dates -- MLB's published
-- regular-season start reproduces the seed on all 44 periods of 2025 and
-- 2026, long opening weeks and both 14-day All-Star periods included. So this
-- is not a tolerance; it is an equality, and any row here is a real finding.
--
-- A period with only one of the two sources is not a disagreement. The
-- derived side is absent for periods ESPN has not closed and for seasons with
-- no captured anchor; the legacy side is absent for every league but the
-- pioneer, and for a stranger entirely. Only rows carrying BOTH can conflict.

select
    league_key,
    season_year,
    matchup_period,
    derived_start_date,
    legacy_start_date,
    derived_end_date,
    legacy_end_date
from {{ ref('dim_matchup_period') }}
where derived_start_date is not null
  and legacy_start_date is not null
  and (derived_start_date <> legacy_start_date
       or derived_end_date <> legacy_end_date)
