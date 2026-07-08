-- Singular test: cbs_stat_map's disposition column is internally
-- consistent (MLB-60).
--   1. disposition = 'mapped'  <=> canonical_key IS NOT NULL. The
--      relationships test validates populated FKs but skips NULLs, so
--      without this a mapped row with a forgotten canonical would pass.
--   2. Every scored category (in_scoring_rules_2026) must be mapped --
--      the ticket's coverage rule: scoring rules can never reference
--      vocabulary the crosswalk doesn't own.
--   3. Scored rows carry their points weight.
--
-- Returns one row per violation; zero rows = pass.

select cbs_key, disposition, canonical_key, in_scoring_rules_2026, points_2026,
       'mapped without canonical / canonical without mapped' as failed_invariant
from {{ ref('cbs_stat_map') }}
where (disposition = 'mapped') != (canonical_key is not null)

union all

select cbs_key, disposition, canonical_key, in_scoring_rules_2026, points_2026,
       'scored category not mapped'
from {{ ref('cbs_stat_map') }}
where in_scoring_rules_2026 and disposition != 'mapped'

union all

select cbs_key, disposition, canonical_key, in_scoring_rules_2026, points_2026,
       'scored category missing points_2026'
from {{ ref('cbs_stat_map') }}
where in_scoring_rules_2026 and points_2026 is null
