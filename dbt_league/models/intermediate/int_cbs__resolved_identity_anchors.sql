-- int_cbs__resolved_identity_anchors.sql
-- WHO IS THIS PLAYER, decided once for every captured CBS year-end roster
-- row (MLB-158 B2, R3). One row per stg_cbs__ui_rosters row -- the same
-- (league_key, season_year, franchise_id, player_name_raw) identity and
-- multiplicity -- carrying the anchor fields reconstruction reads, the raw
-- name kept as an audit label, and the identity decision as NAMED columns.
--
-- Until B2 the roster walk-back and the attribution fact each normalized
-- the anchor name and joined the identity models themselves: the walk-back
-- after assembling its stints, the attribution fact inside its own anchor
-- CTE. Both used the same policy, so it is decided here once and both read
-- it before their own calculations.
--
--   context_*       FRANCHISE-CONTEXT FIRST (MLB-120). An anchor row is
--                   franchise paperwork, so the anchored franchise's earned
--                   or hand-assigned resolution (int_player_identity_context)
--                   applies to it by construction; else the season-only
--                   answer (dim_player_identity at name_key + season_year).
--                   context_ident is that mlbam as text, or 'name:' ||
--                   name_key where unresolved; context_scope_key folds a
--                   NULL scope to ''. The existing MLB-81 keys, unchanged.
--   dictionary_cbs_player_id
--                   The league's own name -> CBS id dictionary
--                   (int_cbs__player_name_ids), set only where the name maps
--                   to exactly one CBS id.
--
-- Anchors carry no platform ids, so the mlbam comes only from the name
-- resolvers, and dim_player_identity counts only candidates with MLB games
-- that season: a rostered player who never played stays unresolved here.
-- This model does not bridge a CBS id to an mlbam through the crosswalk.
--
-- No re-aggregation: the attribution fact groups anchors to its own
-- (ident, scope) grain, and that stays reconstruction's decision.

{{ config(materialized='table', meta={'dag_layer': 'identity'}) }}

with anchors as (
    select
        league_key,
        season_year,
        franchise_id,
        player_name_raw,
        {{ cbs_name_key('player_name_raw') }}   as name_key,
        primary_pos,
        roster_status,
        est_start_share
    from {{ ref('stg_cbs__ui_rosters') }}
)

select
    a.*,

    coalesce(ctx.mlbam_id, pid.mlbam_id)                    as context_mlbam_id,
    coalesce(ctx.stat_group_scope, pid.stat_group_scope)    as context_stat_group_scope,
    coalesce(pid.is_ambiguous, false)
        and ctx.mlbam_id is null                            as context_is_ambiguous,
    coalesce({{ to_varchar('coalesce(ctx.mlbam_id, pid.mlbam_id)') }},
             'name:' || a.name_key)                         as context_ident,
    coalesce(coalesce(ctx.stat_group_scope, pid.stat_group_scope), '') as context_scope_key,

    case when d.n_ids = 1 then d.any_id end                 as dictionary_cbs_player_id
from anchors a
left join {{ ref('dim_player_identity') }} pid
    on pid.platform = 'cbs'
    and pid.name_key = a.name_key
    and pid.season_year = a.season_year
left join {{ ref('int_player_identity_context') }} ctx
    on ctx.platform = 'cbs'
    and ctx.league_key = a.league_key
    and ctx.season_year = a.season_year
    and ctx.franchise_id = a.franchise_id
    and ctx.name_key = a.name_key
left join {{ ref('int_cbs__player_name_ids') }} d
    on d.league_key = a.league_key
    and d.name_key = a.name_key
