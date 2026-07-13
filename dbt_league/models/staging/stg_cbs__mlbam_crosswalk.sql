-- stg_cbs__mlbam_crosswalk.sql
-- The CBS player id -> MLBAM id bridge, staged. PLATFORM-level, not
-- league-level: CBS player ids are stable across the platform, so no
-- league_key rides here -- league scoping enters where membership and
-- scoring rules join (the fantasy layer), not on the identity map.
--
-- The raw table is rebuilt in place by extract/mlb_crosswalk.py
-- (name+season+team-aware; 0 collisions since 2026-07-10), so no
-- latest-snapshot dance -- the table IS the current mapping.
--
-- stat_group_scope implements the MLB-68 two-way decision. CBS represents a
-- two-way player as split pseudo-ids -- Ohtani is 900 'Shohei Ohtani
-- (Batter)' / 901 'Shohei Ohtani (Pitcher)', both mapped to MLBAM 660271,
-- the one sanctioned shared pair -- and suffixes the name with the
-- discipline. The scope column turns that suffix into a join predicate:
-- hitting gamelog rows feed the '(Batter)' id, pitching rows the
-- '(Pitcher)' id, and unsuffixed players (scope NULL) take both groups
-- (a pre-universal-DH pitcher's batting rides their single CBS id).

select
    cbs_player_id,
    cbs_name                as cbs_player_name,
    mlbam_id,
    mlbam_name,
    method                  as match_method,
    case
        when cbs_name ilike '%(batter)%'  then 'hitting'
        when cbs_name ilike '%(pitcher)%' then 'pitching'
    end                     as stat_group_scope
from {{ source('raw', 'cbs_mlbam_crosswalk') }}

union all

-- The UI-HISTORY population (MLB-63 coverage extension): year-end-roster
-- players the CBS archive universe never held, so NO CBS id exists
-- anywhere -- identity is the display name (matched to MLBAM by the same
-- evidence machinery). They join this staging under SYNTHETIC ids
-- ('ui-<mlbam>', collision-proof against real CBS ids) so the calculated_
-- engine prices their games; the walk-back attribution keys by NAME, so
-- the synthetic id never needs to match anything platform-side. Only
-- mlbams the real crosswalk does NOT already carry enter -- a second
-- identity for the same player would fan the engine's grain.
select
    'ui-' || u.mlbam_id     as cbs_player_id,
    u.cbs_name              as cbs_player_name,
    u.mlbam_id,
    u.mlbam_name,
    'ui_' || u.method       as match_method,
    null                    as stat_group_scope
from {{ source('raw', 'cbs_ui_mlbam_xwalk') }} u
where u.mlbam_id is not null
    and u.mlbam_id not in (
        select mlbam_id from {{ source('raw', 'cbs_mlbam_crosswalk') }}
        where mlbam_id is not null
    )
qualify row_number() over (partition by u.mlbam_id order by u.ui_name) = 1
