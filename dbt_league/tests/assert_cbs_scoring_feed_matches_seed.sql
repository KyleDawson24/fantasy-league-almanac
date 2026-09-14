-- Singular test: the platform's scoring_rules feed and the cbs_stat_map
-- seed agree on WHAT is scored and at WHAT weight (MLB-62).
--
-- stg_cbs__scoring_settings reads the feed (the pricing authority);
-- the seed's in_scoring_rules_2026/points_2026 mirror it as curated
-- documentation. The two must never drift: a rule change lands in a new
-- config capture, this test fails, and the seed gets re-curated
-- deliberately (points_2026 is also era-labeled -- a changed weight is a
-- new column decision, not a silent overwrite).
--
-- Compares VERBATIM feed points (feed_points, 3/inning for INN), not the
-- out-granularity effective weight -- the seed documents the feed's
-- surface, the staging model owns the translation.
--
-- SCOPED TO ONE LEAGUE, ON PURPOSE (MLB-219 / MLB-222). The seed columns
-- are one league's configuration -- the one named by the
-- cbs_curated_scoring_league var (dbt_project.yml). This is a
-- documentation-drift check for THAT league, not a correctness invariant
-- for CBS leagues in general: any other league scores different categories
-- at different weights by definition, so its correct feed must never be
-- graded against our seed. Every other league_key is filtered out here,
-- and the `exists` guard makes the test vacuous when the curated league has
-- no feed at all (an ESPN-only install: 0 feed rows, 16 seed rows -- which
-- without the guard reads as 16 "seed scores a category the feed does not"
-- failures). Measured on the ESPN-only fixture, 2026-09-13.
--
-- Returns one row per violation; zero rows = pass.

with feed as (
    select cbs_key, feed_points
    from {{ ref('stg_cbs__scoring_settings') }}
    where league_key = '{{ var("cbs_curated_scoring_league") }}'
),

seed as (
    select cbs_key, points_2026
    from {{ ref('cbs_stat_map') }}
    where in_scoring_rules_2026
)

select
    coalesce(f.cbs_key, s.cbs_key) as cbs_key,
    f.feed_points,
    s.points_2026,
    case
        when f.cbs_key is null then 'seed scores a category the feed does not'
        when s.cbs_key is null then 'feed scores a category the seed does not'
        else 'weight mismatch'
    end as failed_invariant
from feed f
full outer join seed s
    on f.cbs_key = s.cbs_key
where exists (select 1 from feed)
  and (
      f.cbs_key is null
      or s.cbs_key is null
      or f.feed_points != s.points_2026
  )
