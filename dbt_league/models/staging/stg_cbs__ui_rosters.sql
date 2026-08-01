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
    -- The cast is PORTABILITY, not rounding (MLB-10, measured 2026-07-31).
    -- own_pct and start_pct are DECIMAL(38,0) on both engines, but the two
    -- disagree about what dividing two decimals PRODUCES: Snowflake yields
    -- NUMBER(38,6), DuckDB yields DOUBLE. So one engine quantises this
    -- ratio to 1e-6 and the other does not, from identical source text with
    -- no cast in it -- a divergence no dialect table lists, because both
    -- engines accept the SQL and neither is wrong.
    --
    -- It survives all the way to the rendered almanac: est_start_share is
    -- the estimated era's fractional active_weight, so CBS 2018's
    -- SUM(active_weight) was 26915.300372 against DuckDB's
    -- 26915.299906511180, and cells sitting on a rounding boundary flipped
    -- (an ERA of 3.38 vs 3.37, a strikeout count of 200 vs 201).
    --
    -- Spelling Snowflake's own result type explicitly gives both engines
    -- the same one. On Snowflake it is an IDENTITY -- measured over all
    -- 9,932 rows with own_pct > 0, 0 moved -- so no golden can move; on
    -- DuckDB it closes 9,906 of 9,906 divergences.
    case when r.own_pct > 0
         then least(cast(r.start_pct / r.own_pct as decimal(38, 6)),
                    1.0) end as est_start_share,
    r.roster_status,
    r.roster_pos,
    r.eligible_pos,
    r.source_path
from rosters r
inner join name_map m
    on r.league_key = m.league_key
    and r.season_year = m.season_year
    and r.team_name = m.team_name
