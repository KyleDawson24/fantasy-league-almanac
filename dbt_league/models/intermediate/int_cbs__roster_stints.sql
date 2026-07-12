-- int_cbs__roster_stints.sql
-- THE WALK-BACK (MLB-63): historic roster membership reconstructed from
-- move edges + year-end anchors. One row per contiguous stint a player
-- spent on a franchise: (league_key, season_year, franchise_id, name_key,
-- stint_start, stint_end], with how it opened, how it closed, and the
-- anomaly flags that grade it (Kyle's rule: fidelity is per-row and
-- flows, never a hidden aggregate).
--
-- THE ALGORITHM (why anchors, why backward-capable):
--   Drafts were never logged (loaded offline every year), so a season's
--   OPENING rosters exist in no transaction log. The year-end anchor
--   closes the season from the other end: any player on the anchor -- or
--   departing mid-season -- WITHOUT a prior in-season acquisition must
--   have opened the season on that roster. Assembly is a last-event-wins
--   state machine per (season, franchise, player):
--     acquisition = add | trade_in            -> ON at event date
--     departure   = drop | trade-OUT (derived: the counterparty of every
--                   trade_in LOSES that player the same instant) -> OFF
--     synthetic opening acquisition at season start where the record
--     demands one (first event is a departure, or anchored with no
--     acquisition at all);
--     anchored players whose last event says OFF reopen at that event
--     (the log missed a re-add) -- flagged anchor_reopened;
--     ON at season end without anchor confirmation -> missing_departure
--     flag (2003-2025; the 2001-2002 no-anchor era can't check).
--
-- Chronology: effective_date wins over txn_date (retroactive edits);
-- same-day event ORDER falls back to the report's reverse row order
-- (row_seq walks newest-first). Season bounds = the MLB season's actual
-- game-date span (universal layer), so stints speak real dates.
--
-- Identity: name_key (the cbs_name_key normalization) -- anchors carry
-- no ids, so names are the join spine; resolved_cbs_player_id rides
-- where the league's own dictionary is unambiguous, and is_ambiguous_name
-- flags the Will Smith / Luis Garcia class for downstream provenance.
-- 2026 is EXCLUDED: the daily captures own the live season.

{{ config(materialized='table') }}

with season_bounds as (
    select
        season_year,
        min(game_date) as season_start,
        max(game_date) as season_end
    from {{ ref('stg_mlb__player_game') }}
    where season_year between 2001 and 2025
    group by season_year
),

-- Move edges, identity-normalized. Trade-outs are derived rows: the
-- counterparty of a trade_in loses the player at the same moment.
moves as (
    select
        league_key,
        season_year,
        franchise_id,
        {{ cbs_name_key('player_name_raw') }}        as name_key,
        coalesce(effective_date, txn_date)           as event_date,
        row_seq,
        entry_seq,
        case when move_type in ('add', 'trade_in')
             then 'acquisition' else 'departure' end as event_kind,
        move_type                                    as event_detail
    from {{ ref('stg_cbs__ui_transactions') }}
    where season_year between 2001 and 2025
        and move_type in ('add', 'trade_in', 'drop')
        and franchise_id is not null

    union all

    select
        league_key,
        season_year,
        counterparty_franchise_id,
        {{ cbs_name_key('player_name_raw') }},
        coalesce(effective_date, txn_date),
        row_seq,
        entry_seq,
        'departure',
        'trade_out'
    from {{ ref('stg_cbs__ui_transactions') }}
    where season_year between 2001 and 2025
        and move_type = 'trade_in'
        and counterparty_franchise_id is not null
),

anchors as (
    select
        league_key,
        season_year,
        franchise_id,
        {{ cbs_name_key('player_name_raw') }} as name_key
    from {{ ref('stg_cbs__ui_rosters') }}
    group by 1, 2, 3, 4
),

-- The state machine wants one ordered event stream per (season,
-- franchise, player). Synthetic season-start acquisitions cover the
-- unlogged opening rosters.
first_events as (
    select
        league_key, season_year, franchise_id, name_key,
        event_kind as first_kind
    from moves
    qualify row_number() over (
        partition by league_key, season_year, franchise_id, name_key
        order by event_date, row_seq desc, entry_seq desc
    ) = 1
),

openings as (
    -- Anchored, no acquisition-first history: on the roster since day 1.
    select
        a.league_key, a.season_year, a.franchise_id, a.name_key,
        'opening' as opening_reason
    from anchors a
    left join first_events f
        on a.league_key = f.league_key and a.season_year = f.season_year
        and a.franchise_id = f.franchise_id and a.name_key = f.name_key
    where f.name_key is null

    union all

    -- First recorded event is a DEPARTURE: they must have opened the
    -- season here (anchored or not -- covers 2001-2002 too).
    select
        league_key, season_year, franchise_id, name_key, 'opening'
    from first_events
    where first_kind = 'departure'
),

events as (
    select
        league_key, season_year, franchise_id, name_key,
        event_date, row_seq, entry_seq, event_kind, event_detail
    from moves

    union all

    select
        o.league_key, o.season_year, o.franchise_id, o.name_key,
        b.season_start, 1000000, 0, 'acquisition', o.opening_reason
    from openings o
    inner join season_bounds b on o.season_year = b.season_year
),

-- Last-event-wins: keep only state CHANGES, giving a strictly
-- alternating acquisition/departure sequence per partition. Same-day
-- ordering: the report walks newest-first, so higher row_seq = earlier
-- that day (synthetic openings sort first via row_seq 1e6... which walk
-- DESC places at the dawn of the season-start day).
ordered as (
    select
        e.*,
        lag(event_kind) over (
            partition by league_key, season_year, franchise_id, name_key
            order by event_date, row_seq desc, entry_seq desc
        ) as prev_kind
    from events e
),

changes as (
    select * from ordered
    where event_kind != coalesce(prev_kind, 'departure')
),

paired as (
    select
        c.league_key,
        c.season_year,
        c.franchise_id,
        c.name_key,
        c.event_date   as stint_start,
        c.event_detail as open_channel,
        lead(c.event_date) over (
            partition by c.league_key, c.season_year, c.franchise_id, c.name_key
            order by c.event_date, c.row_seq desc, c.entry_seq desc
        )              as next_change_date,
        lead(c.event_detail) over (
            partition by c.league_key, c.season_year, c.franchise_id, c.name_key
            order by c.event_date, c.row_seq desc, c.entry_seq desc
        )              as next_change_detail
    from changes c
    where c.event_kind = 'acquisition'
),

stints as (
    select
        p.league_key,
        p.season_year,
        p.franchise_id,
        p.name_key,
        row_number() over (
            partition by p.league_key, p.season_year, p.franchise_id, p.name_key
            order by p.stint_start
        )                                              as stint_index,
        p.stint_start,
        coalesce(p.next_change_date, b.season_end)     as stint_end,
        p.open_channel,
        coalesce(p.next_change_detail, 'season_end')   as close_type,
        (a.name_key is not null)                       as on_year_end_anchor
    from paired p
    inner join season_bounds b on p.season_year = b.season_year
    left join anchors a
        on p.league_key = a.league_key and p.season_year = a.season_year
        and p.franchise_id = a.franchise_id and p.name_key = a.name_key
),

-- Anomalies grade each (player, franchise, season) record.
flags as (
    select
        league_key, season_year, franchise_id, name_key,
        -- Anchored but the log's last word was a departure: the log
        -- missed a re-acquisition. (Their season_end stint is absent --
        -- surfaced as a flag, not silently invented.)
        boolor_agg(close_type = 'season_end') as ends_on_roster
    from stints
    group by 1, 2, 3, 4
)

select
    s.league_key,
    s.season_year,
    s.franchise_id,
    s.name_key,
    s.stint_index,
    s.stint_start,
    s.stint_end,
    s.open_channel,
    s.close_type,
    s.on_year_end_anchor,
    -- Fidelity flags (Kyle's provenance rule -- carried per row):
    (s.close_type = 'season_end' and not s.on_year_end_anchor
        and s.season_year >= 2003)                     as missing_departure,
    (s.on_year_end_anchor and not f.ends_on_roster)    as anchor_reopen_needed,
    (s.season_year < 2003)                             as no_anchor_era,
    (d.n_ids > 1)                                      as is_ambiguous_name,
    case when d.n_ids = 1 then d.any_id end            as resolved_cbs_player_id
from stints s
inner join flags f
    on s.league_key = f.league_key and s.season_year = f.season_year
    and s.franchise_id = f.franchise_id and s.name_key = f.name_key
left join {{ ref('int_cbs__player_name_ids') }} d
    on s.league_key = d.league_key and s.name_key = d.name_key
