-- int_cbs__roster_stints_effective.sql
-- Single-rostering enforcement over the reconstructed stints (MLB-63):
-- a player is on ONE roster at a time, so a stint that runs open past
-- the player's next acquisition ELSEWHERE (the lossy-drop class -- the
-- platform's log simply omits thousands of drops) truncates to the day
-- before that acquisition. Correctly-closed stints are untouched (their
-- end already precedes the next start). was_truncated joins the
-- provenance vocabulary: it marks membership whose END is inferred
-- rather than recorded.

-- Attribution windows are [stint_start, attribution_end_exclusive):
-- a departure DAY belongs to the receiving side (CBS effective-date
-- semantics -- the drop/trade effective 6/15 means out of the lineup
-- 6/15), while season_end closes stay inclusive. Truncation caps at the
-- player's next acquisition date (the new team owns that day).

{{ config(materialized='view') }}

with stints as (
    select
        s.*,
        case when s.close_type in ('drop', 'trade_out')
             then s.stint_end
             else {{ date_add_unit('day', 1, 's.stint_end') }}
        end as base_end_exclusive,
        -- Single-rostering runs on the PLAYER, so key on the MLB-81 identity
        -- (mlbam), not the name form: K-Rod's 'francisco rodriguez' and
        -- 'francisco j rodriguez' stints are one player and must dedupe as
        -- one. NULL mlbam (ambiguous/uncandidated) falls back to the name key
        -- -- today's behaviour. The scope term keeps a two-way player's two
        -- halves (Ohtani 2025: batter on one franchise, pitcher on another) as
        -- INDEPENDENT single-rostering streams.
        coalesce({{ to_varchar('s.mlbam_id') }}, 'name:' || s.name_key) as _ident,
        coalesce(s.stat_group_scope, '')                        as _scope
    from {{ ref('int_cbs__roster_stints') }} s
),

-- The next acquisition STRICTLY LATER than this stint's own start (MLB-117).
--
-- This was a lead() ordered by (stint_start, franchise_id). That ordering is
-- not unique, and when two stints for one player share both -- the same
-- franchise adding them on the same day under two name forms ('carlos a
-- hernandez' / 'carlos e hernandez', 'tony a pena' / 'tony f pena'), or a
-- same-day add/drop sitting beside a real rostering -- lead() was free to put
-- either first. Whichever landed first was truncated to zero length by the
-- other, and the choice could change between builds. A full season of a real
-- player's games then flipped between his franchise and the holding pen (or,
-- outside 2001-02 where no holding pen exists, vanished from the fact
-- altogether). 11 stint groups across 2002-2024 sat on that coin-flip.
--
-- min() over strictly-later starts fixes it at the root rather than papering
-- over it with a tiebreaker: two stints starting on the SAME day can no longer
-- truncate each other in either direction, so there is nothing left to order.
-- Genuine truncation is untouched -- a later acquisition still ends this
-- stint's attribution, whether the player moved ELSEWHERE (the lossy-drop
-- class this model exists for) or the same franchise re-added him after a gap.
--
-- Same-day moves between franchises need no truncation: those stints close
-- with 'drop'/'trade_out', so base_end_exclusive already ends them that day.
next_acquisition as (
    select
        b.league_key,
        b.season_year,
        b.franchise_id,
        b.name_key,
        b.stint_index,
        min(o.stint_start) as next_acquisition_start
    from stints b
    join stints o
        on o.league_key  = b.league_key
        and o.season_year = b.season_year
        and o._ident      = b._ident
        and o._scope      = b._scope
        and o.stint_start > b.stint_start
    group by 1, 2, 3, 4, 5
),

base as (
    select
        s.*,
        n.next_acquisition_start
    from stints s
    left join next_acquisition n
        on n.league_key   = s.league_key
        and n.season_year  = s.season_year
        and n.franchise_id = s.franchise_id
        and n.name_key     = s.name_key
        and n.stint_index  = s.stint_index
)

select
    b.* exclude (_ident, _scope),
    least(b.base_end_exclusive,
          coalesce(b.next_acquisition_start, b.base_end_exclusive))
        as attribution_end_exclusive,
    least(b.base_end_exclusive,
          coalesce(b.next_acquisition_start, b.base_end_exclusive))
        < b.base_end_exclusive as was_truncated
from base b
