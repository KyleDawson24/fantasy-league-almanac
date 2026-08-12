-- assert_configured_name_has_no_active_collision.sql
-- WARN: two teams that were both playing in the same season share one
-- configured canonical name.
--
-- Collapsing ids onto a configured name is the RULE, not the bug -- that is
-- how a franchise rebuilt without a lineage row stays one team. But two
-- distinct franchises fielding teams in the SAME season cannot be one team,
-- so a shared configured name across them is almost always a typo in the
-- lineage seed: a name pasted onto the wrong row, or a second era named
-- before the first was retired.
--
-- The model still aggregates them, because the rule has to mean the same
-- thing everywhere. This is the diagnostic that lets the league notice and
-- correct it.
--
-- WARN, NOT ERROR, DELIBERATELY. The rule produces a defined, documented
-- answer here; nothing is corrupt and nothing is ambiguous. Failing the build
-- over a display-seed typo would stop a weekly run for a cosmetic problem,
-- and a gate people learn to skip is worse than a warning people read.
{{ config(severity='warn') }}

select
    league_key,
    season_year,
    identity_key,
    identity_name,
    count(distinct canonical_franchise_id) as colliding_franchises,
    -- Ordered so the reported list is the same on both engines and between
    -- runs: this string is what a human reads to find the offending seed rows.
    {{ listagg_ordered('canonical_franchise_id', ', ',
                       'canonical_franchise_id', distinct=True) }} as franchise_ids
from {{ ref('dim_franchise_identity') }}
where identity_source = 'configured_name'
group by league_key, season_year, identity_key, identity_name
having count(distinct canonical_franchise_id) > 1
