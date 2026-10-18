-- int_cbs__resolved_identity_events.sql
-- WHO IS THIS PLAYER, decided once for every captured CBS transaction row
-- (MLB-158 B2, R2). One row per stg_cbs__ui_transactions row -- the same
-- (league_key, season_year, row_seq, entry_seq) identity and multiplicity,
-- every move type and every season -- carrying the event fields the
-- reconstruction models replay, the raw name kept as an audit label, and
-- the identity decision as NAMED columns, one set per resolution policy.
--
-- Until B2 the lineup intervals and the roster walk-back each normalized
-- the name and joined the identity models themselves. Identity is upstream
-- of "what happened on day D" (docs/dag-boundaries.md section 2), so the
-- decision lives here and reconstruction reads it. Which rows a consumer
-- replays (move types, seasons, franchise present) stays the consumer's
-- business; this model filters nothing.
--
-- The policies give DIFFERENT answers on purpose and stay separate columns,
-- so a consumer cannot drift onto another's policy by picking a column:
--
--   season_*        SEASON-ONLY. dim_player_identity at (name_key,
--                   season_year): the mlbam the name form resolves to that
--                   season, NULL when ambiguous or uncandidated. The lineup
--                   intervals' policy.
--   context_*       FRANCHISE-CONTEXT FIRST (MLB-120). The acting
--                   franchise's earned or hand-assigned resolution from
--                   int_player_identity_context, else the season-only
--                   answer. The roster walk-back's policy: a stint is
--                   franchise-scoped, so the context id applies exactly there.
--   counterparty_context_*
--                   The same policy at the SENDING franchise of a trade_in
--                   row. The walk-back derives that side's trade_out from
--                   this row, so its identity is decided here as well.
--   dictionary_cbs_player_id
--                   The league's own name -> CBS id dictionary
--                   (int_cbs__player_name_ids), set only where the name maps
--                   to exactly one CBS id.
--
-- The unresolved fallback is the name itself: season_ident is the mlbam
-- where resolved, else 'name:' || name_key, and season_scope_key folds a
-- NULL scope to ''. These are the existing MLB-81 keys, moved here
-- unchanged; nothing in this model is a new matching rule.

{{ config(materialized='table', meta={'dag_layer': 'identity'}) }}

with events as (
    select
        league_key,
        season_year,
        row_seq,
        entry_seq,
        txn_row_key,
        txn_ts,
        txn_date,
        effective_date,
        franchise_id,
        counterparty_franchise_id,
        move_type,
        from_slot,
        to_slot,
        player_cbs_id,
        player_name_raw,
        {{ cbs_name_key('player_name_raw') }}   as name_key
    from {{ ref('stg_cbs__ui_transactions') }}
)

select
    e.*,

    pid.mlbam_id                                            as season_mlbam_id,
    pid.stat_group_scope                                    as season_stat_group_scope,
    coalesce(pid.is_ambiguous, false)                       as season_is_ambiguous,
    coalesce({{ to_varchar('pid.mlbam_id') }}, 'name:' || e.name_key) as season_ident,
    coalesce(pid.stat_group_scope, '')                      as season_scope_key,

    coalesce(ctx.mlbam_id, pid.mlbam_id)                    as context_mlbam_id,
    coalesce(ctx.stat_group_scope, pid.stat_group_scope)    as context_stat_group_scope,
    coalesce(pid.is_ambiguous, false)
        and ctx.mlbam_id is null                            as context_is_ambiguous,

    coalesce(cp.mlbam_id, pid.mlbam_id)                     as counterparty_context_mlbam_id,
    coalesce(cp.stat_group_scope, pid.stat_group_scope)     as counterparty_context_stat_group_scope,
    coalesce(pid.is_ambiguous, false)
        and cp.mlbam_id is null                             as counterparty_context_is_ambiguous,

    case when d.n_ids = 1 then d.any_id end                 as dictionary_cbs_player_id
from events e
left join {{ ref('dim_player_identity') }} pid
    on pid.platform = 'cbs'
    and pid.name_key = e.name_key
    and pid.season_year = e.season_year
left join {{ ref('int_player_identity_context') }} ctx
    on ctx.platform = 'cbs'
    and ctx.league_key = e.league_key
    and ctx.season_year = e.season_year
    and ctx.franchise_id = e.franchise_id
    and ctx.name_key = e.name_key
left join {{ ref('int_player_identity_context') }} cp
    on cp.platform = 'cbs'
    and cp.league_key = e.league_key
    and cp.season_year = e.season_year
    and cp.franchise_id = e.counterparty_franchise_id
    and cp.name_key = e.name_key
left join {{ ref('int_cbs__player_name_ids') }} d
    on d.league_key = e.league_key
    and d.name_key = e.name_key
