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
