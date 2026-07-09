-- stg_cbs__player_season_stats.sql
-- CBS adapter (#2) player-season performance, LONG form and canonical-bridged:
-- one row per (league_key, season_year, player_id, stat_name). The season-grain
-- half of F1 -- the raw material for the CBS record book (best player seasons
-- under the league's own scoring). Per-GAME performance (gamelogs) + the
-- calculated-lens recompute ride the next models; this reads the season
-- universes directly, where the platform's own season FPTS already lives (no
-- recompute needed for the platform lens).
--
-- The bridge in action: each player object's native CBS stat keys are
-- unpivoted, mapped cbs_key -> canonical_key (cbs_stat_map) -> stat_name
-- (dim_stat), so CBS emits the SAME stat_name vocabulary the ESPN DAG speaks
-- and the shared dim_stat metadata (polarity, display, record-candidacy)
-- applies downstream. Unmapped keys (metadata, derived composites, unbridged
-- vocabulary) drop out at the INNER JOINs -- intended.
--
-- Two universes share this model, distinguished at source by params.position:
-- the hitter universe (no position param) carries hitting stats + hitting
-- FPTS, the pitcher universe (position=P) carries pitching stats + pitching
-- FPTS. In this pure-category league a player scores in one universe (verified:
-- zero real-id overlap 2004-2025), so (player_id, season_year, stat_name) is
-- unique; `universe` rides as context. The two-way sentinels (900/901 Ohtani)
-- are absent from both universes and don't appear here -- their records come
-- from the gamelog path, not the season universe.
--
-- Note INN vs OUTS: the display key INN (innings_pitched) has no ESPN
-- stat_name and drops; OUTS (the scoring primitive) bridges to stat_name OUTS
-- and carries the innings signal. Season FPTS bridges to PLATFORM_POINTS.

with universes as (
    select
        league_key,
        season_year,
        coalesce(params:position::string, 'H') as universe_code,
        payload
    from {{ source('raw', 'cbs_season_stats') }}
    qualify row_number() over (
        partition by league_key, season_year, coalesce(params:position::string, 'H')
        order by loaded_at desc
    ) = 1
),

players as (
    select
        u.league_key,
        u.season_year,
        case when u.universe_code = 'P' then 'pitching' else 'hitting' end as universe,
        p.value as player
    from universes u,
        lateral flatten(input => u.payload:body:league_stats:players) p
    where p.value:id is not null
),

stat_values as (
    select
        pl.league_key,
        pl.season_year,
        pl.universe,
        pl.player:id::string   as player_id,
        pl.player:name::string as player_name,
        kv.key                 as cbs_key,
        -- Strip thousands separators; non-numeric values (e.g. PPos 'SP')
        -- become NULL and drop below -- but those keys aren't 'mapped'
        -- crosswalk stats anyway.
        try_to_double(replace(kv.value::string, ',', '')) as stat_value
    from players pl,
        lateral flatten(input => pl.player) kv
)

select
    sv.league_key,
    sv.season_year,
    sv.player_id,
    sv.player_name,
    sv.universe,
    d.stat_name,
    d.stat_category,
    sv.stat_value
from stat_values sv
inner join {{ ref('cbs_stat_map') }} m
    on sv.cbs_key = m.cbs_key
    and m.disposition = 'mapped'
inner join {{ ref('dim_stat') }} d
    on m.canonical_key = d.canonical_key
where sv.stat_value is not null
