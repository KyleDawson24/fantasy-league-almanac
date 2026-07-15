-- dim_player_identity.sql
-- PLATFORM-GENERAL player identity resolver (MLB-81). Maps every name form a
-- fantasy platform writes -- across its report families, era name drift,
-- middle initials, two-way splits and hard renames -- to the universal MLBAM
-- id, so the historic walk-back can attribute id-first instead of by fragile
-- per-join name equality (the class that lost 94% of K-Rod's career and left
-- the middle-initial cohort 36% attributed).
--
-- Grain: (platform, name_key, season_year) -> the mlbam that name resolves to
-- THAT season. Season-scoped because disambiguation is inherently seasonal:
-- "Mike Stanton" is the 1989-2007 reliever in 2005 and (now-Giancarlo) Stanton
-- in 2011 -- two different mlbams, one name string, separated by which one
-- actually played that year. Every consumer seam (stints, anchors, lineup
-- intervals) already carries season_year, so this joins 1:1.
--
-- Design for reuse (this is the one identity map, not a CBS-only table): the
-- mlbam spine is universal, so every future platform (Yahoo, Fantrax, ...) runs
-- the SAME exercise and feeds the SAME table under its own `platform` value.
-- Only the CANDIDATE block below is platform-specific; variant expansion and
-- the season resolver are platform-agnostic. It also stands alone as a novel,
-- shareable cross-platform name/id <-> MLBAM map -- the hard disambiguations
-- ("which Mike Stanton is which") recorded once, reusable everywhere.
--
-- Resolution contract:
--   * exactly one mlbam maps to (name_key, season) and played that season
--     -> mlbam_id resolved, is_ambiguous = false.
--   * two-plus map and played the same season (genuinely ambiguous homonyms,
--     the overlapping-Luis-Garcia class) -> mlbam_id NULL, is_ambiguous = true;
--     the attribution seam keeps its name-join + discipline-preference tie-break
--     for these, exactly as before -- never a silent guess.
--   * none played that season -> no row (the seam's LEFT JOIN yields NULL mlbam
--     -> name fallback), which is today's behaviour preserved.
--   stat_group_scope rides from the source name's crosswalk scope: a two-way
--   split id contributes its own discipline; a UNIFIED entry that pulls both
--   disciplines collapses to NULL = "matches either" (Ohtani, and any future
--   two-way -- driven by data, never a per-player literal).

{{ config(materialized='table') }}

with

-- ===================================================================
-- CBS candidate block (platform='cbs'). Each row asserts "this name form
-- means this mlbam (+ discipline scope)" from an id-bearing CBS source.
-- A new platform adds its own analogous block to this union.
-- ===================================================================
xw as (
    select cbs_player_id, cbs_player_name, mlbam_id, mlbam_name, stat_group_scope
    from {{ ref('stg_cbs__mlbam_crosswalk') }}
),

cbs_named as (
    -- record-book / display name (the games' own cbs_player_name)
    select 'cbs' as platform, cbs_player_id as platform_player_id,
           mlbam_id, stat_group_scope, cbs_player_name as name_raw
    from xw
    union all
    -- official MLBAM name -- bridges the Kendrick "Howard"/"Howie" and
    -- Morales "Kendry"/"Kendrys" class with NO alias needed
    select 'cbs', cbs_player_id, mlbam_id, stat_group_scope, mlbam_name
    from xw
    union all
    -- id-bearing transaction log (~2013+): the log's own name form tied to
    -- mlbam through the player's crosswalk id. Current-pool only -- a departed
    -- player's real log id has no crosswalk row, so those ride the variants.
    select 'cbs', xw.cbs_player_id, xw.mlbam_id, xw.stat_group_scope, t.player_name_raw
    from {{ ref('stg_cbs__ui_transactions') }} t
    join xw on t.player_cbs_id = xw.cbs_player_id
    where t.player_cbs_id is not null
    union all
    -- 2026 daily captures
    select 'cbs', xw.cbs_player_id, xw.mlbam_id, xw.stat_group_scope, r.player_name
    from {{ ref('stg_cbs__rosters') }} r
    join xw on r.player_id = xw.cbs_player_id
),

named as (
    select platform, platform_player_id, mlbam_id, stat_group_scope,
           {{ cbs_name_key('name_raw') }} as base_key
    from cbs_named
    where name_raw is not null
),

-- Variant expansion: each ADDITIONAL row is another name form pointing at the
-- SAME mlbam, never a replacement. A collision only makes a (name_key, season)
-- ambiguous (handled by the resolver), never a silent bad merge.
named_variants as (
    select platform, platform_player_id, mlbam_id, stat_group_scope, base_key as name_key
    from named

    union all
    -- middle-initial stripped: 'francisco j rodriguez' -> 'francisco rodriguez'
    -- (K-Rod, Bautista, V. Martinez, J. Reyes, Carlos E. Hernandez, ...)
    select platform, platform_player_id, mlbam_id, stat_group_scope,
           regexp_replace(base_key, '^([a-z]+) [a-z] (.+)$', '\\1 \\2')
    from named
    where regexp_like(base_key, '^[a-z]+ [a-z] .+$')

    union all
    -- two-way parenthetical stripped: 'shohei ohtani (batter)' -> 'shohei ohtani'
    -- lets a UNIFIED roster entry resolve to the split-id mlbam; the scope
    -- collapse below then yields NULL = "either discipline" for such an entry.
    select platform, platform_player_id, mlbam_id, stat_group_scope,
           trim(regexp_replace(base_key, ' \\((batter|pitcher)\\)$', ''))
    from named
    where base_key like '% (batter)' or base_key like '% (pitcher)'
    -- NOTE: ASCII accent-folding is a future variant if an accented-only bridge
    -- ever surfaces; today every CBS cbs_name form is plain ASCII and the
    -- mlbam_name candidate covers the rest, so folding adds nothing yet.
),

-- Alias seed: hard renames whose forms share no token (Carmona->Hernandez,
-- Mike Stanton->Giancarlo). Baseball-truth, platform-independent; scope comes
-- from the aliased mlbam's crosswalk row(s).
alias_candidates as (
    select 'cbs' as platform,
           max(xw.cbs_player_id)  as platform_player_id,
           a.mlbam_id,
           xw.stat_group_scope,
           a.name_key
    from {{ ref('player_alias') }} a
    left join xw on xw.mlbam_id = a.mlbam_id
    group by a.mlbam_id, xw.stat_group_scope, a.name_key
),

-- The INVERSE middle-initial case (Miggy, not K-Rod): the initial rides on the
-- id-less LOG/ANCHOR side, not the crosswalk. CBS's 2003-06 roster pages wrote
-- "Miguel M Cabrera" -> 'miguel m cabrera' while the crosswalk carries plain
-- "Miguel Cabrera"; those forms live only in id-less sources so they can't tie
-- to mlbam by id. Bridge them by matching their middle-initial-STRIPPED form to
-- a crosswalk name (the symmetric completion of the strip variant above, which
-- only covered crosswalk-side initials). Restricted to forms that actually
-- carry a middle initial so plain names don't round-trip.
xw_keys as (
    select distinct name_key, mlbam_id, stat_group_scope from named_variants
),

idless_forms as (
    select distinct {{ cbs_name_key('player_name_raw') }} as name_key
    from {{ ref('stg_cbs__ui_transactions') }}
    where player_cbs_id is null and player_name_raw is not null
    union
    select distinct {{ cbs_name_key('player_name_raw') }} as name_key
    from {{ ref('stg_cbs__ui_rosters') }}
    where player_name_raw is not null
),

idless_bridged as (
    select 'cbs' as platform, cast(null as varchar) as platform_player_id,
           x.mlbam_id, x.stat_group_scope, f.name_key
    from idless_forms f
    join xw_keys x
        on x.name_key = regexp_replace(f.name_key, '^([a-z]+) [a-z] (.+)$', '\\1 \\2')
    where regexp_like(f.name_key, '^[a-z]+ [a-z] .+$')
),

candidates as (
    select platform, platform_player_id, mlbam_id, stat_group_scope, name_key from named_variants
    union all
    select platform, platform_player_id, mlbam_id, stat_group_scope, name_key from alias_candidates
    union all
    select platform, platform_player_id, mlbam_id, stat_group_scope, name_key from idless_bridged
),

-- Per (platform, name_key, mlbam): collapse discipline scope. Multiple scopes
-- (Ohtani's paren-stripped 'shohei ohtani' pulls hitting from id 900 + pitching
-- from id 901) -> NULL = "matches either discipline"; a single scope survives.
by_name_mlbam as (
    select
        platform, name_key, mlbam_id,
        case when count(distinct stat_group_scope) > 1 then null
             else max(stat_group_scope) end   as stat_group_scope,
        max(platform_player_id)               as platform_player_id
    from candidates
    where name_key is not null and name_key <> ''
    group by platform, name_key, mlbam_id
),

-- MLB game-presence: which mlbam actually played which season -- the seasonal
-- disambiguator (the reliever Stanton plays no season Giancarlo does).
game_presence as (
    select distinct mlbam_id, season_year
    from {{ ref('stg_mlb__player_game') }}
),

resolved as (
    select
        b.platform,
        b.name_key,
        gp.season_year,
        count(distinct b.mlbam_id)   as n_mlbam,
        max(b.mlbam_id)              as rep_mlbam,
        max(b.stat_group_scope)      as rep_scope,
        max(b.platform_player_id)    as rep_platform_player_id
    from by_name_mlbam b
    join game_presence gp on gp.mlbam_id = b.mlbam_id
    group by b.platform, b.name_key, gp.season_year
)

select
    platform,
    name_key,
    season_year,
    case when n_mlbam = 1 then rep_mlbam end               as mlbam_id,
    case when n_mlbam = 1 then rep_scope end               as stat_group_scope,
    case when n_mlbam = 1 then rep_platform_player_id end  as platform_player_id,
    (n_mlbam > 1)                                          as is_ambiguous,
    n_mlbam
from resolved
