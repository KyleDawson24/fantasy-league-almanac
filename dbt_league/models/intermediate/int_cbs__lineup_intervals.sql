-- int_cbs__lineup_intervals.sql
-- Daily ACTIVE/RESERVE state for the lineup-log eras (2001-2003, 2021+),
-- reconstructed as intervals: each activate/reserve event opens a state
-- that holds until the player's next lineup event on that franchise.
-- slot_move events are repositioning WITHIN the active lineup and touch
-- the slot, not the state; compound verbs ('Activated and Moved to U')
-- already normalized to their state verb.
--
-- State at a stint's open: reserve until first activated (CBS lands
-- added players on the reserve; documented assumption -- in these eras
-- an activation follows within days and self-corrects). Opening-roster
-- players inherit whatever the PRE-season lineup moves set (the logs
-- begin in March, before first pitch).
--
-- Membership-only eras (2004-2020) have no rows here by construction --
-- their active state is ESTIMATED downstream via est_start_share.

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
        case when move_type = 'activate' then 'A' else 'RS' end as state
    from {{ ref('stg_cbs__ui_transactions') }}
    where move_type in ('activate', 'reserve')
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

-- THE BACKWARD HALF: state BEFORE a player's first lineup event is the
-- INVERSE of what that event sets -- a first event of 'Benched' proves
-- they had been active until then (the set-and-forget starters the
-- forward walk alone zeroes out).
select
    league_key,
    season_year,
    franchise_id,
    name_key,
    '0001-01-01'::date,
    event_date,
    iff(state = 'A', 'RS', 'A'),
    'prior_inverse'
from with_next
where event_order = 1
