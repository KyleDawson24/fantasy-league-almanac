-- dim_franchise_identity.sql
-- The AGGREGATION identity of a franchise-season (MLB-229). Which team, for
-- the purpose of adding things up, is this platform id in this season?
--
-- ==========================================================================
-- WHY THIS IS NOT dim_franchise_season
-- ==========================================================================
-- dim_franchise_season answers "which franchise is this id?" and stops at the
-- lineage seed's re-mint links. That is the right answer for provenance and
-- the wrong one for a rivalry ledger, because a league can hold ONE team
-- across ids the lineage never linked -- a franchise that was rebuilt from
-- scratch, an id retired and replaced without a continuity row, two eras the
-- historian records separately but the league thinks of as one team.
--
-- Kyle's ruling: an explicitly CONFIGURED canonical name IS the identity.
-- Platform ids identify source records; the configured name identifies the
-- team. So two franchises carrying the same configured name are one team here
-- even when their canonical_franchise_ids differ, and historical names and
-- re-minted ids roll into it.
--
-- THE FALLBACK IS AN ID, NEVER A NAME. Where no configured name exists, the
-- identity is the canonical franchise id and the best observed name is a
-- LABEL hung on it. Two unconfigured franchises whose observed names happen to
-- match stay separate -- observation is a coincidence, configuration is a
-- statement. This is the single most important asymmetry in the model, and it
-- is why the key is prefix-tagged: `name:` and `fid:` live in one column and
-- can never collide, so no fallback can be mistaken for a configured identity
-- or merged into one.
--
-- MATCHING IS EXACT, AFTER TRIMMING. Two configured names collapse when the
-- trimmed strings are equal -- no case folding, no punctuation smoothing. A
-- league that wrote two different strings meant two different things, and a
-- normalisation that quietly merged 'Bent Spokes' with 'bent spokes' would be
-- this model inventing an identity rather than reading one. Trailing
-- whitespace is the one difference nobody means, so trim is the whole of it.
--
-- ACCIDENTAL COLLISIONS ARE AGGREGATED AND REPORTED. If two teams that are
-- both playing right now share one configured name, the rule still applies --
-- they aggregate -- and assert_configured_name_has_no_active_collision warns
-- so the league can correct the seed. Warn, not error: the rule produces a
-- defined answer, and a build that refuses to complete over a typo in a
-- display seed helps nobody.
--
-- ACTIVITY IS NOT HERE. Whether an identity is still active is a question
-- about the AXES of a rendered matrix, not about what a fact aggregates into
-- (mart_franchise_rivalry_axes). An active team keeps every result its former
-- ids and names ever earned.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id, season_year).
-- ==========================================================================
{{ config(materialized='view') }}

with franchise_seasons as (
    select
        league_key,
        franchise_id,
        season_year,
        canonical_franchise_id,
        canonical_name,
        canonical_abbrev,
        configured_name,
        configured_abbrev,
        has_configured_name
    from {{ ref('dim_franchise_season') }}
),

keyed as (
    select
        *,
        -- The prefix is load-bearing, not decoration: it is what keeps a
        -- configured name and a franchise id in one comparable column without
        -- a league that names a team '13' colliding with franchise 13.
        case
            when has_configured_name
                then 'name:' || trim(configured_name)
            else 'fid:' || canonical_franchise_id
        end as identity_key,
        case when has_configured_name
             then 'configured_name' else 'franchise_id' end as identity_source
    from franchise_seasons
),

-- Display for the whole identity, resolved ONCE per key rather than per
-- franchise-season. A configured identity displays what the league wrote. A
-- fallback identity displays its canonical franchise's best observed name --
-- which is constant across that franchise's seasons already, so the aggregate
-- is a pick, not a vote.
identity_display as (
    select
        league_key,
        identity_key,
        max(case when identity_source = 'configured_name'
                 then trim(configured_name) else canonical_name end)
            as identity_name,
        max(case when identity_source = 'configured_name'
                 then configured_abbrev else canonical_abbrev end)
            as identity_abbrev
    from keyed
    group by league_key, identity_key
)

select
    k.league_key,
    k.franchise_id,
    k.season_year,
    k.canonical_franchise_id,
    k.identity_key,
    k.identity_source,
    d.identity_name,
    d.identity_abbrev,
    -- Kept for provenance: what the franchise-grain dims say, next to what
    -- the identity rule made of it.
    k.canonical_name,
    k.configured_name
from keyed k
join identity_display d
    on k.league_key = d.league_key
    and k.identity_key = d.identity_key
