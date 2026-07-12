-- int_cbs__player_name_ids.sql
-- The name -> CBS-player-id dictionary (MLB-63 identity), built from the
-- league's OWN id-bearing rows -- no external matching:
--   - the modern transaction log links players (~2013+), in both era name
--     forms ('Rich Hill' and 'Sanchez, Jonathan');
--   - the 2026 daily roster captures (id + 'First Last');
--   - the CBS->MLBAM crosswalk's cbs_name census.
--
-- One row per (league_key, name_key): the distinct ids CBS itself has
-- attached to that display name, with n_ids > 1 marking AMBIGUOUS names
-- (the league has had two Will Smiths and three Luis Garcias). The
-- walk-back keys stints by normalized name (anchors carry no ids at all)
-- and uses this dictionary to (a) resolve unambiguous names to ids for
-- the points join and (b) flag ambiguous ones -- per Kyle's provenance
-- rule, ambiguity is a FLAG that flows, never a silent guess.
--
-- name_key normalization mirrors what the walk-back applies to anchors
-- and moves: packed era forms ('Teagarden, Taylor C TEX') lose their
-- trailing POS + MLB-team tokens, 'Last, First' flips, case folds, and
-- periods drop ('Sanchez, Jonathan O.' == 'jonathan o sanchez').

{{ config(materialized='view') }}

with pairs as (

    select
        league_key,
        player_cbs_id  as cbs_player_id,
        player_name_raw as name_raw
    from {{ ref('stg_cbs__ui_transactions') }}
    where player_cbs_id is not null

    union all

    select
        league_key,
        player_id,
        player_name
    from {{ ref('stg_cbs__rosters') }}

    union all

    select
        'cbs-bsb' as league_key,   -- the crosswalk is platform-level
        cbs_player_id,
        cbs_player_name
    from {{ ref('stg_cbs__mlbam_crosswalk') }}
),

normalized as (
    select
        league_key,
        cbs_player_id,
        {{ cbs_name_key('name_raw') }} as name_key
    from pairs
    where name_raw is not null
)

select
    league_key,
    name_key,
    array_agg(distinct cbs_player_id) as cbs_player_ids,
    count(distinct cbs_player_id)     as n_ids,
    max(cbs_player_id)                as any_id
from normalized
group by league_key, name_key
