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

with base as (
    select
        s.*,
        case when s.close_type in ('drop', 'trade_out')
             then s.stint_end
             else dateadd(day, 1, s.stint_end)
        end as base_end_exclusive,
        -- Single-rostering runs on the PLAYER, so partition by the MLB-81
        -- identity (mlbam), not the name form: K-Rod's 'francisco rodriguez'
        -- and 'francisco j rodriguez' stints are one player and must dedupe
        -- as one. NULL mlbam (ambiguous/uncandidated) falls back to the name
        -- key -- today's behaviour. The scope term keeps a two-way player's
        -- two halves (Ohtani 2025: batter on one franchise, pitcher on
        -- another) as INDEPENDENT single-rostering streams.
        lead(s.stint_start) over (
            partition by s.league_key, s.season_year,
                         coalesce(to_varchar(s.mlbam_id), 'name:' || s.name_key),
                         coalesce(s.stat_group_scope, '')
            order by s.stint_start, s.franchise_id
        ) as next_acquisition_start
    from {{ ref('int_cbs__roster_stints') }} s
)

select
    b.*,
    least(b.base_end_exclusive,
          coalesce(b.next_acquisition_start, b.base_end_exclusive))
        as attribution_end_exclusive,
    least(b.base_end_exclusive,
          coalesce(b.next_acquisition_start, b.base_end_exclusive))
        < b.base_end_exclusive as was_truncated
from base b
