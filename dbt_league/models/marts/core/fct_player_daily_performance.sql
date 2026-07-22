-- fct_player_daily_performance.sql
-- Comprehensive player-daily performance fact. Thin view over
-- int_player_daily that adds two grain-defining status columns
-- (performance_status, wasted_bucket) so all downstream weekly facts
-- can inherit them without re-deriving.
--
-- Grain: (league_key, season_year, scoring_period, team_id, player_id, lineup_slot).
-- The "comprehensive" framing: every slot, every roster status, the
-- real-world MLB stat line with fantasy roster context attached. Active
-- vs. inactive splits happen at the weekly fact layer; this layer
-- carries everything.
--
-- v1.1.0 DAG cleanup: introduces a consumer-safe daily contract so
-- output scripts and league_notes callouts that need per-day data
-- (HR streaks, no-negative-days, SP-start counting, get_wasted_points
-- metadata snapshot) can read from a mart-layer model rather than
-- reaching back into int_player_daily.
--
-- performance_status / wasted_bucket derivation mirrors what previously
-- lived in int_player_weekly_performance. Pulling it down to the daily
-- layer means both daily and weekly consumers see the same status
-- semantics from one canonical computation.
--
-- MLB-72: int_player_daily now unions the CBS day-grain branch, so this
-- fact serves both leagues. CBS rows introduce two lineup_slot codes the
-- derivations fold in ('RS' reserve -> inactive; 'EST' estimated state ->
-- the new 'estimated' performance_status, with active_weight carrying the
-- start-share estimator) plus the union-layer passthroughs (player_key,
-- game_date, active_weight, provenance). ESPN rows never emit RS/EST, so
-- every ESPN-visible value is byte-identical to the pre-union fact.
--
-- Materialization: view. int_player_daily is itself a view at ~600K
-- rows; adding another view over it costs nothing at storage and
-- queries within real workload latency. Flip to table if a future
-- consumer makes daily queries hot enough to matter.

{{ config(materialized='view') }}

with base as (

select
    -- Identifiers
    league_key,
    season_year,
    matchup_period,
    scoring_period,
    team_id,
    team_name,
    team_abbrev,
    owner_name,
    player_id,
    player_key,
    player_name,
    display_name,
    position,
    pro_team,
    eligible_slots,
    lineup_slot,
    lineup_slot_category,
    games_played,
    game_date,

    -- Status flags. performance_status mirrors the WHERE clause on the
    -- active weekly fact (lineup_slot NOT IN BE/IL/FA -> 'active'),
    -- redundant with is_active_slot but with the consumer-facing label
    -- string used across mart_stat_leaderboard. wasted_bucket
    -- distinguishes the two flavors of inactive: 'FA' for free agents,
    -- 'ROSTERED_INACTIVE' for BE/IL (and CBS's RS), NULL for active.
    -- CBS's estimated era gets its own status: neither active nor
    -- inactive is knowable per-day there -- consumers weight by
    -- active_weight instead (fct_player_position_pts.weighted_active_pts).
    is_active_slot,
    case
        when lineup_slot in ('BE', 'IL', 'FA', 'RS') then 'inactive'
        when lineup_slot = 'EST'                     then 'estimated'
        else 'active'
    end as performance_status,
    case
        when lineup_slot = 'FA'                then 'FA'
        when lineup_slot in ('BE', 'IL', 'RS') then 'ROSTERED_INACTIVE'
        else null
    end as wasted_bucket,
    active_weight,
    provenance,

    -- Platform totals
    platform_points,
    platform_hitting_pts,
    platform_pitching_pts,
    negative_points,

    -- Hitting counting
    h, ab, b_bb, b_so, hbp, sf, hr, r, rbi,
    sb, cs, tb, singles, doubles, triples, xbh,
    gdp, b_ibb, cyc,

    -- Hitting point contributions
    h_pts, ab_pts, b_bb_pts, b_so_pts, hbp_pts, sf_pts,
    hr_pts, r_pts, rbi_pts, sb_pts, cs_pts, tb_pts,
    singles_pts, doubles_pts, triples_pts, xbh_pts,
    gdp_pts, b_ibb_pts, cyc_pts,

    -- Pitching counting
    w, l, k, er, outs, qs, sv, hld,
    p_h, p_bb, p_hr, p_r, cg, blk, wp,
    hbp_p, blsv, nh, pg, pk, sho,

    -- Pitching point contributions
    w_pts, l_pts, k_pts, er_pts, outs_pts, qs_pts,
    sv_pts, hld_pts, p_h_pts, p_bb_pts, p_hr_pts, p_r_pts,
    cg_pts, blk_pts, wp_pts, hbp_p_pts, blsv_pts,
    nh_pts, pg_pts, pk_pts, sho_pts,

    -- Catch-all totals
    total_hitting_stat_pts,
    total_pitching_stat_pts,
    total_stat_pts

from {{ ref('int_player_daily') }}

)

-- TEAM DISPLAY RESOLVES HERE, for both books at once (MLB-113). Every column
-- above carries the name the PLATFORM reported for that row, straight out of
-- RAW. That is the wrong source for display: it ignores the franchise override
-- layer, so a rename never propagates backward, a re-minted franchise wears two
-- names across its own history, and -- the reason this became urgent -- an
-- anonymized render leaks the real names it was supposed to replace.
--
-- This is the single seam for the whole player/team fact chain. int_player_daily
-- has already converged both books by this point, and every downstream fact and
-- mart inherits team_name/team_abbrev from here, so resolving once reaches the
-- chain without a platform branch anywhere.
--
-- Joined at SEASON grain: a franchise-season the lineage seed reassigns (a
-- platform reusing a live team id, MLB-115) displays what it actually was that
-- year rather than what the id became later.
--
-- * REPLACE swaps the two values in place so column order is untouched --
-- int_player_daily's UNION is positional by design and downstream models select
-- by name against this shape.
--
-- COALESCE is a safety net, not a fallback we expect to lean on: an unresolved
-- franchise keeps its platform name rather than blanking a real one. Free
-- agents (team_id NULL) miss the join by construction and keep their NULLs.
-- assert_team_display_resolves_through_dim proves the net stays load-free.
select b.* replace (
    coalesce(d.canonical_name, b.team_name)     as team_name,
    coalesce(d.canonical_abbrev, b.team_abbrev) as team_abbrev
)
from base b
left join {{ ref('dim_franchise_season') }} d
    on b.league_key = d.league_key
    and cast(b.team_id as varchar) = d.franchise_id
    and b.season_year = d.season_year
