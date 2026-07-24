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
-- OVERRIDES (MLB-120): `player_identity_overrides` is the human escape
-- hatch -- one row repoints a NAME FORM to the mlbam a person confirmed,
-- upstream of everything (display names, dim_player_identity candidates,
-- the attribution name-fallback all read this staging). First use: the ui
-- matcher collided BOTH 2004 Gonzalezes onto Juan (114932) -- its own
-- method column says fuzzy_overlap_COLLISION -- and the per-mlbam dedupe
-- then picked 'Jeremi' as Juan's display name. The override repoints
-- 'jeremi gonzalez' -> Geremi's real 114928 (Kyle-verified), which both
-- unmasks Juan's display name and ends the phantom contest. Overrides
-- apply BEFORE the ui branch's per-mlbam dedupe so the corrected mlbam
-- partitions the dedupe.
--
-- stat_group_scope implements the MLB-68 two-way decision. CBS represents a
-- two-way player as split pseudo-ids -- Ohtani is 900 'Shohei Ohtani
-- (Batter)' / 901 'Shohei Ohtani (Pitcher)', both mapped to MLBAM 660271,
-- the one sanctioned shared pair -- and suffixes the name with the
-- discipline. The scope column turns that suffix into a join predicate:
-- hitting gamelog rows feed the '(Batter)' id, pitching rows the
-- '(Pitcher)' id, and unsuffixed players (scope NULL) take both groups
-- (a pre-universal-DH pitcher's batting rides their single CBS id).

with overrides as (
    select cbs_name_key, mlbam_id, mlbam_name
    from {{ ref('player_identity_overrides') }}
    where platform = 'cbs'
),

main_x as (
    select
        x.cbs_player_id,
        x.cbs_name                            as cbs_player_name,
        coalesce(o.mlbam_id, x.mlbam_id)      as mlbam_id,
        iff(o.mlbam_id is not null, o.mlbam_name, x.mlbam_name) as mlbam_name,
        iff(o.mlbam_id is not null, 'override', x.method)       as match_method
    from {{ source('raw', 'cbs_mlbam_crosswalk') }} x
    left join overrides o
        on o.cbs_name_key = {{ cbs_name_key('x.cbs_name') }}
),

ui_x as (
    select
        u.cbs_name,
        u.ui_name,
        coalesce(o.mlbam_id, u.mlbam_id)      as mlbam_id,
        iff(o.mlbam_id is not null, o.mlbam_name, u.mlbam_name) as mlbam_name,
        iff(o.mlbam_id is not null, 'ui_override', 'ui_' || u.method) as match_method
    from {{ source('raw', 'cbs_ui_mlbam_xwalk') }} u
    left join overrides o
        on o.cbs_name_key = {{ cbs_name_key('u.cbs_name') }}
    where u.mlbam_id is not null
)

select
    cbs_player_id,
    cbs_player_name,
    mlbam_id,
    mlbam_name,
    match_method,
    case
        when cbs_player_name ilike '%(batter)%'  then 'hitting'
        when cbs_player_name ilike '%(pitcher)%' then 'pitching'
    end                     as stat_group_scope
from main_x

union all

-- The UI-HISTORY population (MLB-63 coverage extension): year-end-roster
-- players the CBS archive universe never held, so NO CBS id exists
-- anywhere -- identity is the display name (matched to MLBAM by the same
-- evidence machinery). They join this staging under SYNTHETIC ids
-- ('ui-only-<mlbam>', collision-proof against real CBS ids; the prefix
-- reads as 'exists in the UI history ONLY -- no platform id anywhere') so the calculated_
-- engine prices their games; the walk-back attribution keys by NAME, so
-- the synthetic id never needs to match anything platform-side. Only
-- mlbams the real crosswalk does NOT already carry enter -- a second
-- identity for the same player would fan the engine's grain.
select
    'ui-only-' || u.mlbam_id as cbs_player_id,
    u.cbs_name              as cbs_player_name,
    u.mlbam_id,
    u.mlbam_name,
    u.match_method,
    null                    as stat_group_scope
from ui_x u
where u.mlbam_id not in (
        select mlbam_id from main_x
        where mlbam_id is not null
    )
qualify row_number() over (partition by u.mlbam_id order by u.ui_name) = 1
