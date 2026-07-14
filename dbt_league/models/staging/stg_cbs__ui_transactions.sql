-- stg_cbs__ui_transactions.sql
-- The league's full movement record, normalized (MLB-54): one row per
-- player-action from the parsed transaction history, 2001-2026 -- the
-- moves the MLB-63 walk-back replays backwards from the year-end anchors.
--
-- Era verb vocabulary -> move_type (the census that fixed this is
-- empirical, all 52k actions):
--   add       Added | Signed (the 2001-era verb; 2001's 572 Signs are the
--                             league's founding roster construction)
--   drop      Dropped | Released (2001-era)
--   trade_in  'Traded from {counterparty}' ('Trades from' in 2008) -- the
--             RECEIVING side; a trade is one transaction id across two
--             per-side rows, so both directions exist as trade_in rows.
--   activate  Activated (+ 'Activated and Moved to {slot}')
--   reserve   Reserved (2001-era) | Benched (+ 'Benched and Moved to...')
--   slot_move 'Moved from {slot} to {slot}' | 'Moved to {slot}' -- active-
--             lineup repositioning; from/to slots carried where stated.
-- Unmapped phrases land move_type NULL and FAIL the accepted-values test
-- loudly -- new-era vocabulary must be classified, never dropped.
--
-- THE LINEUP-LOG FIDELITY MAP (empirical, drives MLB-63's per-season
-- grades): 2001-2003 and 2021+ carry activate/reserve/slot moves ->
-- day-grain active sets reconstruct. 2004-2020 retained MEMBERSHIP moves
-- only -> membership reconstructs day-grain, active status is estimated
-- (see stg_cbs__ui_rosters.est_start_share) and must flag as such.
--
-- Ordering: row_seq walks each season NEWEST-FIRST (the report's order),
-- so ascending (row_seq, entry_seq) IS walk-back order; chronological
-- replay sorts descending. txn_ts parses where the era carries a time
-- (2004+); the 2001-era log is date-grain. effective_date is the
-- SCORING-relevant date where present (it can precede the timestamp --
-- retroactive lineup edits).
--
-- Identity: player_cbs_id is present from ~2013 (33.5k rows) and joins
-- the calculated_ lens directly. Earlier eras are name-only -- the
-- walk-back joins those to the year-end anchors per (season, franchise,
-- name); a global name->id pass rides the crosswalk machinery later.
-- team_franchise_id: the modern /teams/{id} link shares the franchise id
-- space (verified by example); name-resolved via the per-season map
-- otherwise. 2026 rows resolve by link only (no final standings yet).

with txns as (
    select
        s.*,
        -- Chronology. MOST eras render the timestamp client-side: the
        -- cell is a document.write(formatTime('...', <unix epoch>))
        -- script, so the EPOCH is the real (timezone-exact) record --
        -- converted to the league's wall clock (ET). The remaining eras
        -- render plain text ('9/29/21 2:05 PM ET', nbsp-separated in
        -- some years -- char(160) normalized below) or the 2001-era
        -- date-only '10/02/01'.
        case
            when s.txn_ts_raw like '%formatTime%'
                then convert_timezone(
                    'America/New_York',
                    to_timestamp_tz(
                        regexp_substr(s.txn_ts_raw, '(\\d{10})\\)',
                                      1, 1, 'e')::integer)
                )::timestamp_ntz
            else try_to_timestamp_ntz(
                regexp_replace(replace(s.txn_ts_raw, char(160), ' '),
                               '\\s*ET$', ''),
                'MM/DD/YY HH12:MI AM')
        end as txn_ts_parsed
    from {{ source('raw', 'cbs_ui_transactions') }} s
),

name_map as (
    select league_key, season_year, team_name, franchise_id
    from {{ ref('stg_cbs__ui_standings') }}
),

-- Trade counterparty names are rendered by CBS as a LIVE lookup of the
-- franchise's CURRENT name, not its era name (verified: 'Firefly Lake
-- Veronicas' counterparties appear in 2021-25 logs but in no year-end
-- standings; 'Baltic White Sox' shows as a 2021 counterparty though the
-- name first exists in 2022). So counterparty resolution falls back to
-- the current-name map from the live period standings when the era map
-- misses. Era-true mid-season names ('Armonk Artillery' 2002) resolve
-- via neither and stay NULL pending the franchise-overview parse
-- (MLB-64's alias work).
current_names as (
    select league_key, team_name, team_id::integer as franchise_id
    from {{ ref('stg_cbs__standings') }}
    qualify row_number() over (
        partition by league_key, team_name
        order by season_year desc, period desc
    ) = 1
)

select
    t.league_key,
    t.season_year,
    t.row_seq,
    t.entry_seq,
    t.txn_row_key,

    t.txn_ts_parsed                                  as txn_ts,
    coalesce(
        to_date(t.txn_ts_parsed),
        try_to_date(split_part(replace(t.txn_ts_raw, char(160), ' '), ' ', 1),
                    'MM/DD/YY'))                     as txn_date,
    try_to_date(t.effective_date_raw, 'MM/DD/YY')    as effective_date,

    -- The acting team.
    coalesce(t.team_id::integer, m.franchise_id)     as franchise_id,
    t.team_name,

    -- The player (id from ~2013; name-only before).
    t.player_cbs_id,
    t.player_name_raw,
    t.player_pos_team_raw,

    -- The normalized move.
    -- Casing drifts by era ('Activated and Moved to U' vs 'Activated and
    -- moved to DH'), so patterned matches are case-insensitive. 'Sent
    -- Down' is the rare demotion verb -- roster-inactive, reserve-class.
    case
        when t.action_raw in ('Added', 'Signed')                 then 'add'
        when t.action_raw in ('Dropped', 'Released')             then 'drop'
        when t.action_raw ilike 'Traded from %'
          or t.action_raw ilike 'Trades from%'                   then 'trade_in'
        when t.action_raw = 'Activated'
          or t.action_raw ilike 'Activated and Moved to %'       then 'activate'
        when t.action_raw in ('Reserved', 'Benched', 'Sent Down')
          or t.action_raw ilike 'Benched and Moved to %'         then 'reserve'
        when t.action_raw ilike 'Moved from % to %'
          or t.action_raw ilike 'Moved to %'                     then 'slot_move'
    end                                              as move_type,
    t.action_raw,

    -- Slot transitions where the era states them. to_slot was DOUBLY broken
    -- until 2026-07-14 (Law 2 made it load-bearing): the WHEN gate matched
    -- neither pattern for 'Moved from X to Y' (no leading space; doesn't
    -- START with 'Moved to'), and the old regex used a non-capture group
    -- (?:...) that Snowflake's engine rejects -- it errored on any row the
    -- gate DID pass. The ' to (\\S+)$' form is POSIX-safe and covers all
    -- three verb shapes (Moved from X to Y | Moved to Y | ... and Moved to Y).
    case
        when t.action_raw ilike 'Moved from % to %'
            then trim(regexp_substr(t.action_raw, 'Moved from (\\S+) to', 1, 1, 'ie'))
    end                                              as from_slot,
    case
        when t.action_raw ilike 'Moved from % to %'
          or t.action_raw ilike '% Moved to %'
          or t.action_raw ilike 'Moved to %'
            then trim(regexp_substr(t.action_raw, ' to (\\S+)$', 1, 1, 'ie'))
    end                                              as to_slot,

    -- Trade counterparty ('Traded from X' names the SENDING team, in that
    -- season's name), franchise-resolved where final standings exist.
    case
        when t.action_raw like 'Traded from %'
            then trim(substr(t.action_raw, len('Traded from ') + 1))
        when t.action_raw like 'Trades from%'
            then nullif(trim(substr(t.action_raw, len('Trades from') + 1)), '')
    end                                              as counterparty_team_name,
    coalesce(cm.franchise_id, cur_m.franchise_id)    as counterparty_franchise_id,

    t.source_path
from txns t
left join name_map m
    on t.league_key = m.league_key
    and t.season_year = m.season_year
    and t.team_name = m.team_name
left join name_map cm
    on t.league_key = cm.league_key
    and t.season_year = cm.season_year
    and cm.team_name = case
        when t.action_raw like 'Traded from %'
            then trim(substr(t.action_raw, len('Traded from ') + 1))
        when t.action_raw like 'Trades from%'
            then nullif(trim(substr(t.action_raw, len('Trades from') + 1)), '')
    end
left join current_names cur_m
    on t.league_key = cur_m.league_key
    and cur_m.team_name = case
        when t.action_raw like 'Traded from %'
            then trim(substr(t.action_raw, len('Traded from ') + 1))
        when t.action_raw like 'Trades from%'
            then nullif(trim(substr(t.action_raw, len('Trades from') + 1)), '')
    end
