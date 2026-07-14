-- int_cbs__lineup_intervals.sql
-- Daily ACTIVE/RESERVE state for the lineup-log eras (2001-2003, 2021+),
-- reconstructed as intervals: each lineup event opens a state that holds
-- until the player's next lineup event on that franchise.
--
-- LAW 2 (Kyle, 2026-07-14): the transaction log is a state machine and
-- EVERY lineup event is a boundary observation -- including slot_move,
-- which the first cut discarded as "repositioning that doesn't touch the
-- state". It does better than touch it: from_slot DIRECTLY observes the
-- state before the boundary and to_slot the state after. Empirical slot
-- census (all eras): every observed token is an active lineup slot
-- (P/SP/RP/C/1B/2B/3B/SS/OF/DH/U) -- bench/IL exits ride the Activate/
-- Reserve verbs -- so slot polarity defaults active, with a defensive
-- inactive-token list should a future era print one.
--
-- State at a stint's open: reserve until first activated (CBS lands
-- added players on the reserve; documented assumption -- in these eras
-- an activation follows within days and self-corrects). Opening-roster
-- players inherit whatever the PRE-season lineup moves set (the logs
-- begin in March, before first pitch).
--
-- Membership-only eras (2004-2020) have no rows here by construction --
-- their active state is ESTIMATED downstream via est_start_share. (A
-- stray slot_move in those logs would land a reconstructed day-state,
-- which the attribution CASE prefers over the estimate -- strictly more
-- knowledge, honestly labeled.)

{{ config(materialized='view') }}

with lineup_events as (
    select
        league_key,
        season_year,
        franchise_id,
        {{ cbs_name_key('player_name_raw') }}       as name_key,
        coalesce(effective_date, txn_date)          as event_date,
        row_seq,
        entry_seq,
        -- State AFTER the boundary.
        case
            when move_type = 'activate' then 'A'
            when move_type = 'reserve'  then 'RS'
            when coalesce(to_slot, '') in ('BE', 'BN', 'RS', 'RES', 'IL', 'DL', 'MIN')
                                        then 'RS'
            else 'A'
        end as state,
        -- State BEFORE the boundary (consumed for first events only):
        -- slot_move's from_slot is a DIRECT observation; activate/reserve
        -- imply the inverse of what they set.
        case
            when move_type = 'slot_move'
                then iff(coalesce(from_slot, '') in ('BE', 'BN', 'RS', 'RES', 'IL', 'DL', 'MIN'),
                         'RS', 'A')
            when move_type = 'activate' then 'RS'
            else 'A'
        end as prior_state,
        iff(move_type = 'slot_move', 'prior_direct', 'prior_inverse') as prior_source
    from {{ ref('stg_cbs__ui_transactions') }}
    where move_type in ('activate', 'reserve', 'slot_move')
        and franchise_id is not null
        and season_year between 2001 and 2025
),

with_next as (
    select
        e.*,
        lead(e.event_date) over (
            partition by e.league_key, e.season_year, e.franchise_id, e.name_key
            order by e.event_date, e.row_seq desc, e.entry_seq desc
        ) as next_event_date,
        row_number() over (
            partition by e.league_key, e.season_year, e.franchise_id, e.name_key
            order by e.event_date, e.row_seq desc, e.entry_seq desc
        ) as event_order
    from lineup_events e
)

select
    league_key,
    season_year,
    franchise_id,
    name_key,
    event_date                    as state_start,
    coalesce(next_event_date, '9999-12-31'::date) as state_end_exclusive,
    state,
    'lineup_event'                as state_source
from with_next

union all

-- THE BACKWARD HALF: state BEFORE a player's first lineup event. A first
-- slot_move OBSERVES it (from_slot: RJ's P->SP proves active all along);
-- a first activate/reserve implies the INVERSE of what it sets (a first
-- 'Benched' proves they had been active until then -- the set-and-forget
-- starters the forward walk alone zeroes out).
select
    league_key,
    season_year,
    franchise_id,
    name_key,
    '0001-01-01'::date,
    event_date,
    prior_state,
    prior_source
from with_next
where event_order = 1
