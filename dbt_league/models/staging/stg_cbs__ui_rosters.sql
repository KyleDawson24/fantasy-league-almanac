-- stg_cbs__ui_rosters.sql
-- Year-end roster ANCHORS from the parsed UI history (MLB-55): one row per
-- (league_key, season_year, franchise_id, player) -- who ENDED each season
-- on each roster, with the deployed slot and the year-end active/reserve
-- state. These are the anchor states the MLB-63 walk-back reconstructs
-- backwards from; they are CONFIRMED membership (not estimates).
--
-- Franchise identity: the raw rows carry team NAMES only (the parse
-- discovered the filename ids are lies and the modern pages are
-- league-wide), so franchise_id resolves through the per-season name map
-- (stg_cbs__ui_standings). INNER join -- a roster team that fails to
-- resolve would silently vanish, so the row-count test below pins the
-- full 10,449.
--
-- Own% / Start% semantics (Kyle, 2026-07-12, verified in data): these are
-- GLOBAL percentages across ALL CBS leagues, and ABSOLUTE (not
-- of-those-who-rostered). Verified: 723 rostered players sit below the
-- 1-in-16 local floor; 37 rostered players show own_pct = 0; start_pct
-- never exceeds own_pct. So the right activity estimator where rostering
-- is KNOWN (every row here) is start_pct / own_pct -- "given rostered,
-- how often started" -- exposed as est_start_share. NULL when own_pct is
-- 0/NULL (undefined -- lowest confidence). Downstream facts that lean on
-- it must carry an estimated-vs-confirmed provenance flag (the
-- interactive surface filters on it; the Sheets almanac explains it).

with rosters as (
    select * from {{ source('raw', 'cbs_ui_rosters') }}
),

name_map as (
    select league_key, season_year, team_name, franchise_id
    from {{ ref('stg_cbs__ui_standings') }}
)

select
    r.league_key,
    r.season_year,
    m.franchise_id,
    r.team_name,
    r.owner_name,
    r.player_name_raw,
    r.player_name,
    r.primary_pos,
    r.mlb_team,
    r.mlb_status,
    r.own_pct,
    r.start_pct,
    case when r.own_pct > 0
         then least(r.start_pct / r.own_pct, 1.0) end as est_start_share,
    r.roster_status,
    r.roster_pos,
    r.eligible_pos,
    r.source_path
from rosters r
inner join name_map m
    on r.league_key = m.league_key
    and r.season_year = m.season_year
    and r.team_name = m.team_name
