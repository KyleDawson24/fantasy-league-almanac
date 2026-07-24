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
-- MLB-120 split: the candidate pipeline (id-bearing sources, variant
-- expansion, alias seed, idless bridging) now lives in
-- int_player_identity_candidates, byte-identically -- this model is the thin
-- season resolver over it. The split exists because franchise-context
-- disambiguation (int_player_identity_context) needs the candidate SET of an
-- ambiguous name, which this model's n_mlbam count deliberately discards.
--
-- Resolution contract:
--   * exactly one mlbam maps to (name_key, season) and played that season
--     -> mlbam_id resolved, is_ambiguous = false.
--   * two-plus map and played the same season (genuinely ambiguous homonyms,
--     the overlapping-Luis-Garcia class) -> mlbam_id NULL, is_ambiguous = true;
--     downstream, int_player_identity_context may still resolve these
--     PER-FRANCHISE from the franchise's own pos/club evidence -- an earned
--     resolution, never a guess; whatever it cannot earn stays ambiguous.
--   * none played that season -> no row (the seam's LEFT JOIN yields NULL mlbam
--     -> name fallback), which is today's behaviour preserved.
--   stat_group_scope rides from the source name's crosswalk scope: a two-way
--   split id contributes its own discipline; a UNIFIED entry that pulls both
--   disciplines collapses to NULL = "matches either" (Ohtani, and any future
--   two-way -- driven by data, never a per-player literal).

{{ config(materialized='table') }}

with

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
    from {{ ref('int_player_identity_candidates') }} b
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
