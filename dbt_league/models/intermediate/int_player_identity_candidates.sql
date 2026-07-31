-- int_player_identity_candidates.sql
-- The identity dim's CANDIDATE layer, split out (MLB-120): every (platform,
-- name_key) -> mlbam assertion from id-bearing sources, variant expansion and
-- the alias seed -- the full pipeline dim_player_identity used to run inline
-- through its by_name_mlbam CTE, moved verbatim so the candidate SET survives
-- as a queryable seam. dim_player_identity now reads this and stays a thin
-- season resolver (byte-identical output); int_player_identity_context reads
-- it too, because franchise-context disambiguation needs the CANDIDATES of an
-- ambiguous name, which the dim's n_mlbam count discards.
--
-- Grain: (platform, name_key, mlbam_id) with the collapsed discipline scope.
-- Everything here is season-BLIND; seasonality enters downstream via MLB
-- game presence (the dim) or per-season evidence (the context resolver).

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
    where {{ regexp_like('base_key', '^[a-z]+ [a-z] .+$') }}

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
    where {{ regexp_like('f.name_key', '^[a-z]+ [a-z] .+$') }}
),

candidates as (
    select platform, platform_player_id, mlbam_id, stat_group_scope, name_key from named_variants
    union all
    select platform, platform_player_id, mlbam_id, stat_group_scope, name_key from alias_candidates
    union all
    select platform, platform_player_id, mlbam_id, stat_group_scope, name_key from idless_bridged
)

-- Per (platform, name_key, mlbam): collapse discipline scope. Multiple scopes
-- (Ohtani's paren-stripped 'shohei ohtani' pulls hitting from id 900 + pitching
-- from id 901) -> NULL = "matches either discipline"; a single scope survives.
select
    platform, name_key, mlbam_id,
    case when count(distinct stat_group_scope) > 1 then null
         else max(stat_group_scope) end   as stat_group_scope,
    max(platform_player_id)               as platform_player_id
from candidates
where name_key is not null and name_key <> ''
group by platform, name_key, mlbam_id
