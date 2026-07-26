-- int_player_daily.sql
-- Wide daily row per (season, scoring_period, team, player, lineup_slot).
-- Combines per-stat point contributions with per-player ESPN platform totals
-- and player display metadata in one row.
--
-- MLB-72: this is the UNION LAYER -- the ESPN branch below is joined by a
-- UNION ALL of int_cbs__player_daily (CBS day-grain in the same column
-- contract), so every league's daily production flows through one shape
-- into the shared fact family. Four columns exist for the union's sake and
-- are filled by both branches:
--   player_key    the grain identity as VARCHAR (ESPN: player_id stringified;
--                 CBS: the platform id, including 'ui-only-' synthetics that
--                 have no numeric form). player_id stays for ESPN consumers.
--   game_date     the real calendar date (NULL on ESPN rows -- their day
--                 identity is the platform scoring_period).
--   active_weight the day's scoring weight: ESPN 1/0 by slot; CBS 1/0 where
--                 the state is known, the start-share estimator (or NULL)
--                 where 2004-2020 history is estimated.
--   provenance    how we know the day's state ('captured' for everything
--                 platform-served; the walk-back enum for CBS history).
--
-- Grain: (league_key, season_year, scoring_period, team_id, player_KEY,
-- lineup_slot). matchup_period travels as a derived column (functionally
-- determined by (league_key, season_year, scoring_period) via the
-- extract-stamped raw rows) on ESPN rows and is NULL on CBS rows (no
-- historic period boundaries exist). Team and owner names are denormalized
-- for downstream convenience.
--
-- Carries:
--   - Identifier 5-tuple + matchup_period
--   - Player metadata: display_name (nickname-resolved), position, pro_team,
--     eligible_slots, lineup_slot_category, games_played
--   - platform_points: ESPN's per-player per-day total (from stg_box_scores)
--   - Wide counting stats for every scored stat in the seed
--   - Per-stat *_pts: stat_value * current-season points_per_unit, computed
--     via the stg_scoring_settings join. Calculated_* semantic per HANDOFF §7:
--     current weights applied universally, including to historical rows
--     (stg_scoring_settings surfaces only current-season settings, so this is
--     automatic).
--   - Catch-all totals: total_hitting_stat_pts, total_pitching_stat_pts,
--     total_stat_pts (sum across ALL scored stats, robust to future seed
--     additions even if the per-stat *_pts columns don't get extended)
--   - is_active_slot: true when lineup_slot_category != 'inactive'.
--     Downstream active/inactive facts apply the inverse filter at their
--     level; this model carries every slot so both can read from one source.
--   - negative_points: magnitude (positive) sum of all negative point
--     contributions on this day. Stored as magnitude so the leaderboard's
--     ORDER BY stat_value DESC ranks "most damage" naturally.
--
-- The pivot uses seed-side stat_names as CASE keys ('1B'/'2B'/'3B'/'64' for
-- SINGLES/DOUBLES/TRIPLES/SHO) with column aliases as the leaderboard column
-- names. The seed-to-leaderboard translation lives in stat_catalog.py (for
-- Python consumers) and in this CASE-key/alias pattern (for SQL).
--
-- Materialization: view. Daily layer at this scale (~600K rows) reads fine
-- live; the table cost is at the weekly layer one level up. View at daily,
-- table at weekly is the right split.

{{ config(materialized='view') }}

with daily_long as (
    -- Long-form per-stat join chain: INNER JOIN classification
    -- (is_counting=true filter), LEFT JOIN scoring settings
    -- (current-season weights). Slot-stat compatibility filter ensures
    -- hitter-in-pitching-slot mismatches drop appropriately.
    select
        d.league_key,
        d.season_year,
        d.matchup_period,
        d.scoring_period,
        d.team_id,
        d.team_name,
        d.team_abbrev,
        d.owner_name,
        d.player_id,
        d.player_name,
        d.lineup_slot,
        d.lineup_slot_category,
        d.stat_name,
        c.stat_category,
        d.stat_value,
        coalesce(sc.points_per_unit, 0) as points_per_unit,
        d.stat_value * coalesce(sc.points_per_unit, 0) as stat_points
    from {{ ref('stg_player_stat_breakdowns') }} d
    inner join {{ ref('stat_classification') }} c
        on d.stat_name = c.stat_name
    -- League-scoped: scoring weights are each league's own rules.
    left join {{ ref('stg_scoring_settings') }} sc
        on d.stat_name = sc.stat_name
        and d.league_key = sc.league_key
    where c.is_counting = true
        {% if var('strict_slot_validity', true) %}
        and (
            c.stat_category = d.lineup_slot_category
            or c.stat_category = 'fielding'
            or d.lineup_slot_category = 'inactive'
        )
        {% endif %}
),

daily_wide as (
    select
        league_key,
        season_year,
        matchup_period,
        scoring_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        player_id,
        player_name,
        lineup_slot,
        lineup_slot_category,

        -- Hitting counting stats
        sum(case when stat_name = 'H'     then stat_value else 0 end) as h,
        sum(case when stat_name = 'AB'    then stat_value else 0 end) as ab,
        sum(case when stat_name = 'B_BB'  then stat_value else 0 end) as b_bb,
        sum(case when stat_name = 'B_SO'  then stat_value else 0 end) as b_so,
        sum(case when stat_name = 'HBP'   then stat_value else 0 end) as hbp,
        sum(case when stat_name = 'SF'    then stat_value else 0 end) as sf,
        sum(case when stat_name = 'HR'    then stat_value else 0 end) as hr,
        sum(case when stat_name = 'R'     then stat_value else 0 end) as r,
        sum(case when stat_name = 'RBI'   then stat_value else 0 end) as rbi,
        sum(case when stat_name = 'SB'    then stat_value else 0 end) as sb,
        sum(case when stat_name = 'CS'    then stat_value else 0 end) as cs,
        sum(case when stat_name = 'TB'    then stat_value else 0 end) as tb,
        sum(case when stat_name = '1B'    then stat_value else 0 end) as singles,
        sum(case when stat_name = '2B'    then stat_value else 0 end) as doubles,
        sum(case when stat_name = '3B'    then stat_value else 0 end) as triples,
        sum(case when stat_name = 'XBH'   then stat_value else 0 end) as xbh,
        sum(case when stat_name = 'GDP'   then stat_value else 0 end) as gdp,
        sum(case when stat_name = 'B_IBB' then stat_value else 0 end) as b_ibb,
        sum(case when stat_name = '30'    then stat_value else 0 end) as cyc,

        -- Hitting point contributions
        {{ stable_sum("case when stat_name = 'H'     then stat_points else 0 end", none) }} as h_pts,
        {{ stable_sum("case when stat_name = 'AB'    then stat_points else 0 end", none) }} as ab_pts,
        {{ stable_sum("case when stat_name = 'B_BB'  then stat_points else 0 end", none) }} as b_bb_pts,
        {{ stable_sum("case when stat_name = 'B_SO'  then stat_points else 0 end", none) }} as b_so_pts,
        {{ stable_sum("case when stat_name = 'HBP'   then stat_points else 0 end", none) }} as hbp_pts,
        {{ stable_sum("case when stat_name = 'SF'    then stat_points else 0 end", none) }} as sf_pts,
        {{ stable_sum("case when stat_name = 'HR'    then stat_points else 0 end", none) }} as hr_pts,
        {{ stable_sum("case when stat_name = 'R'     then stat_points else 0 end", none) }} as r_pts,
        {{ stable_sum("case when stat_name = 'RBI'   then stat_points else 0 end", none) }} as rbi_pts,
        {{ stable_sum("case when stat_name = 'SB'    then stat_points else 0 end", none) }} as sb_pts,
        {{ stable_sum("case when stat_name = 'CS'    then stat_points else 0 end", none) }} as cs_pts,
        {{ stable_sum("case when stat_name = 'TB'    then stat_points else 0 end", none) }} as tb_pts,
        {{ stable_sum("case when stat_name = '1B'    then stat_points else 0 end", none) }} as singles_pts,
        {{ stable_sum("case when stat_name = '2B'    then stat_points else 0 end", none) }} as doubles_pts,
        {{ stable_sum("case when stat_name = '3B'    then stat_points else 0 end", none) }} as triples_pts,
        {{ stable_sum("case when stat_name = 'XBH'   then stat_points else 0 end", none) }} as xbh_pts,
        {{ stable_sum("case when stat_name = 'GDP'   then stat_points else 0 end", none) }} as gdp_pts,
        {{ stable_sum("case when stat_name = 'B_IBB' then stat_points else 0 end", none) }} as b_ibb_pts,
        {{ stable_sum("case when stat_name = '30'    then stat_points else 0 end", none) }} as cyc_pts,

        -- Pitching counting stats
        sum(case when stat_name = 'W'     then stat_value else 0 end) as w,
        sum(case when stat_name = 'L'     then stat_value else 0 end) as l,
        sum(case when stat_name = 'K'     then stat_value else 0 end) as k,
        sum(case when stat_name = 'ER'    then stat_value else 0 end) as er,
        sum(case when stat_name = 'OUTS'  then stat_value else 0 end) as outs,
        sum(case when stat_name = 'QS'    then stat_value else 0 end) as qs,
        sum(case when stat_name = 'SV'    then stat_value else 0 end) as sv,
        sum(case when stat_name = 'HLD'   then stat_value else 0 end) as hld,
        sum(case when stat_name = 'P_H'   then stat_value else 0 end) as p_h,
        sum(case when stat_name = 'P_BB'  then stat_value else 0 end) as p_bb,
        sum(case when stat_name = 'P_HR'  then stat_value else 0 end) as p_hr,
        sum(case when stat_name = 'P_R'   then stat_value else 0 end) as p_r,
        sum(case when stat_name = 'CG'    then stat_value else 0 end) as cg,
        sum(case when stat_name = 'BLK'   then stat_value else 0 end) as blk,
        sum(case when stat_name = 'WP'    then stat_value else 0 end) as wp,
        sum(case when stat_name = 'HBP_P' then stat_value else 0 end) as hbp_p,
        sum(case when stat_name = 'BLSV'  then stat_value else 0 end) as blsv,
        sum(case when stat_name = 'NH'    then stat_value else 0 end) as nh,
        sum(case when stat_name = 'PG'    then stat_value else 0 end) as pg,
        sum(case when stat_name = 'PK'    then stat_value else 0 end) as pk,
        sum(case when stat_name = '64'    then stat_value else 0 end) as sho,

        -- Pitching point contributions
        {{ stable_sum("case when stat_name = 'W'     then stat_points else 0 end", none) }} as w_pts,
        {{ stable_sum("case when stat_name = 'L'     then stat_points else 0 end", none) }} as l_pts,
        {{ stable_sum("case when stat_name = 'K'     then stat_points else 0 end", none) }} as k_pts,
        {{ stable_sum("case when stat_name = 'ER'    then stat_points else 0 end", none) }} as er_pts,
        {{ stable_sum("case when stat_name = 'OUTS'  then stat_points else 0 end", none) }} as outs_pts,
        {{ stable_sum("case when stat_name = 'QS'    then stat_points else 0 end", none) }} as qs_pts,
        {{ stable_sum("case when stat_name = 'SV'    then stat_points else 0 end", none) }} as sv_pts,
        {{ stable_sum("case when stat_name = 'HLD'   then stat_points else 0 end", none) }} as hld_pts,
        {{ stable_sum("case when stat_name = 'P_H'   then stat_points else 0 end", none) }} as p_h_pts,
        {{ stable_sum("case when stat_name = 'P_BB'  then stat_points else 0 end", none) }} as p_bb_pts,
        {{ stable_sum("case when stat_name = 'P_HR'  then stat_points else 0 end", none) }} as p_hr_pts,
        {{ stable_sum("case when stat_name = 'P_R'   then stat_points else 0 end", none) }} as p_r_pts,
        {{ stable_sum("case when stat_name = 'CG'    then stat_points else 0 end", none) }} as cg_pts,
        {{ stable_sum("case when stat_name = 'BLK'   then stat_points else 0 end", none) }} as blk_pts,
        {{ stable_sum("case when stat_name = 'WP'    then stat_points else 0 end", none) }} as wp_pts,
        {{ stable_sum("case when stat_name = 'HBP_P' then stat_points else 0 end", none) }} as hbp_p_pts,
        {{ stable_sum("case when stat_name = 'BLSV'  then stat_points else 0 end", none) }} as blsv_pts,
        {{ stable_sum("case when stat_name = 'NH'    then stat_points else 0 end", none) }} as nh_pts,
        {{ stable_sum("case when stat_name = 'PG'    then stat_points else 0 end", none) }} as pg_pts,
        {{ stable_sum("case when stat_name = 'PK'    then stat_points else 0 end", none) }} as pk_pts,
        {{ stable_sum("case when stat_name = '64'    then stat_points else 0 end", none) }} as sho_pts,

        -- Catch-all totals across the seed's scored stats (robust to future
        -- per-stat *_pts additions; same pattern as int_player_weekly_performance)
        {{ stable_sum("case when stat_category = 'hitting'  then stat_points else 0 end", none) }} as total_hitting_stat_pts,
        {{ stable_sum("case when stat_category = 'pitching' then stat_points else 0 end", none) }} as total_pitching_stat_pts,
        {{ stable_sum("stat_points", none) }}                                                       as total_stat_pts
        -- negative_points computed in the `final` CTE below where
        -- platform_points is available via the stg_box_scores join.
    from daily_long
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
),

platform_split_basis as (
    -- UNFILTERED per-category stat points, used ONLY to apportion the
    -- platform total across hitting vs pitching (see `final`). Mirrors
    -- daily_long's join chain but deliberately OMITS the strict_slot_validity
    -- filter: a two-way player's off-slot production must survive here so a
    -- DH-slot pitching start (or an SP-slot at-bat) can be attributed to the
    -- right bucket. This basis feeds the platform split ONLY -- the
    -- calculated_* lens still reads the slot-validity-filtered totals from
    -- daily_wide above, so calculated_points continues to reproduce ESPN's
    -- slot-aware team scoring (platform_calculated_delta stays ~0).
    --
    -- For single-role players one category is 0, so the downstream split
    -- collapses to all-or-nothing into the correct bucket -- byte-identical
    -- to the prior slot-based behavior. Only genuine two-way days (Ohtani)
    -- see a real split.
    select
        d.league_key,
        d.season_year,
        d.scoring_period,
        d.team_id,
        d.player_id,
        d.lineup_slot,
        sum(case when c.stat_category = 'hitting'
                 then d.stat_value * coalesce(sc.points_per_unit, 0)
                 else 0 end) as unfiltered_hitting_pts,
        sum(case when c.stat_category = 'pitching'
                 then d.stat_value * coalesce(sc.points_per_unit, 0)
                 else 0 end) as unfiltered_pitching_pts
    from {{ ref('stg_player_stat_breakdowns') }} d
    inner join {{ ref('stat_classification') }} c
        on d.stat_name = c.stat_name
    left join {{ ref('stg_scoring_settings') }} sc
        on d.stat_name = sc.stat_name
        and d.league_key = sc.league_key
    where c.is_counting = true
    group by 1, 2, 3, 4, 5, 6
),

final as (
    -- Join back to stg_box_scores for per-player-day metadata that doesn't
    -- live on stg_player_stat_breakdowns: display_name (nickname-resolved),
    -- position, pro_team, games_played, and the ESPN platform total
    -- (b.points). LEFT JOIN because daily_wide's grain is a subset of
    -- stg_box_scores (zero-stat players don't survive daily_long's INNER
    -- JOIN to classification on a stat_name they don't emit).
    select
        w.league_key,
        w.season_year,
        w.matchup_period,
        w.scoring_period,
        w.team_id,
        w.team_name,
        w.team_abbrev,
        w.owner_name,
        w.player_id,
        w.player_name,
        coalesce(b.display_name, w.player_name) as display_name,
        b.position,
        b.pro_team,
        b.eligible_slots,
        w.lineup_slot,
        w.lineup_slot_category,
        case when w.lineup_slot_category != 'inactive' then true else false end as is_active_slot,
        b.games_played,
        b.points as platform_points,

        -- Stat-contribution split of platform_points into hitting vs
        -- pitching, so the active fact can roll up weekly
        -- platform_hitting_pts / platform_pitching_pts from this layer.
        --
        -- platform_points (b.points) is ESPN's slot-BLIND player-card total:
        -- for a two-way player it includes pitching produced from a hitting
        -- slot (Ohtani's DH-day start) and vice-versa. The prior logic split
        -- all-or-nothing by lineup_slot, which dumped a two-way player's whole
        -- day into ONE bucket (the "Ohtani goof": a DH-slot pitching start
        -- landed entirely in hitting). We instead apportion b.points by the
        -- player's UNFILTERED per-category stat contribution that day
        -- (platform_split_basis), so the total lands in the buckets that
        -- actually earned it.
        --
        -- Single-role players have one category = 0, so the split collapses to
        -- all-or-nothing into the correct bucket -- identical to the old
        -- slot-based result. The denominator-zero branch (no scored stats that
        -- day) falls back to the old slot-based assignment so zero-stat edge
        -- rows stay byte-identical. The two daily branches always sum to
        -- b.points, preserving platform_hitting + platform_pitching =
        -- platform_points.
        --
        -- NOTE: this is the slot-blind player-production view (what the recap
        -- superlatives celebrate). The calculated_* lens stays slot-validity-
        -- filtered (= ESPN's slot-aware TEAM scoring); the two legitimately
        -- diverge for a two-way player by the off-slot production ESPN did not
        -- credit to the team -- the same divergence platform_calculated_delta
        -- captures at team grain.
        case
            when coalesce(u.unfiltered_hitting_pts, 0)
               + coalesce(u.unfiltered_pitching_pts, 0) = 0
                then case when w.lineup_slot in ('SP', 'RP', 'P') then 0 else b.points end
            else b.points * u.unfiltered_hitting_pts
                 / (u.unfiltered_hitting_pts + u.unfiltered_pitching_pts)
        end as platform_hitting_pts,
        case
            when coalesce(u.unfiltered_hitting_pts, 0)
               + coalesce(u.unfiltered_pitching_pts, 0) = 0
                then case when w.lineup_slot in ('SP', 'RP', 'P') then b.points else 0 end
            else b.points * u.unfiltered_pitching_pts
                 / (u.unfiltered_hitting_pts + u.unfiltered_pitching_pts)
        end as platform_pitching_pts,

        -- Wide stats (passthrough)
        w.h, w.ab, w.b_bb, w.b_so, w.hbp, w.sf, w.hr, w.r, w.rbi,
        w.sb, w.cs, w.tb, w.singles, w.doubles, w.triples, w.xbh,
        w.gdp, w.b_ibb, w.cyc,
        w.h_pts, w.ab_pts, w.b_bb_pts, w.b_so_pts, w.hbp_pts, w.sf_pts,
        w.hr_pts, w.r_pts, w.rbi_pts, w.sb_pts, w.cs_pts, w.tb_pts,
        w.singles_pts, w.doubles_pts, w.triples_pts, w.xbh_pts,
        w.gdp_pts, w.b_ibb_pts, w.cyc_pts,
        w.w, w.l, w.k, w.er, w.outs, w.qs, w.sv, w.hld,
        w.p_h, w.p_bb, w.p_hr, w.p_r, w.cg, w.blk, w.wp,
        w.hbp_p, w.blsv, w.nh, w.pg, w.pk, w.sho,
        w.w_pts, w.l_pts, w.k_pts, w.er_pts, w.outs_pts, w.qs_pts,
        w.sv_pts, w.hld_pts, w.p_h_pts, w.p_bb_pts, w.p_hr_pts, w.p_r_pts,
        w.cg_pts, w.blk_pts, w.wp_pts, w.hbp_p_pts, w.blsv_pts,
        w.nh_pts, w.pg_pts, w.pk_pts, w.sho_pts,

        w.total_hitting_stat_pts,
        w.total_pitching_stat_pts,
        w.total_stat_pts,

        -- Per-day net-negative platform_points magnitude. Stored UNSIGNED
        -- (positive) so the leaderboard ranks "most damage" naturally with
        -- ORDER BY DESC. Aggregates via SUM at the weekly layer: for a
        -- player with one -5 day and net-positive week, the weekly rollup
        -- = 5 (the -5 magnitude). Matches the existing doubly_wasted_pts
        -- concept (consumer-side: GREATEST(0, -week_active_platform_pts))
        -- when all the player's negative days fall in active slots, and is
        -- finer-grained otherwise (separately attributes each negative day).
        case when b.points < 0 then -b.points else 0 end as negative_points,

        -- Union-layer columns (see the header). ESPN state is always
        -- platform-served, hence 'captured' / binary weight.
        to_varchar(w.player_id) as player_key,
        cast(null as date)      as game_date,
        iff(w.lineup_slot_category != 'inactive', 1.0, 0.0) as active_weight,
        'captured'              as provenance
    from daily_wide w
    left join {{ ref('stg_box_scores') }} b
        on w.league_key = b.league_key
        and w.season_year = b.season_year
        and w.scoring_period = b.scoring_period
        and w.team_id is not distinct from b.team_id
        and w.player_id = b.player_id
        and w.lineup_slot = b.lineup_slot
    -- Unfiltered per-category basis for the platform hitting/pitching split.
    -- Always matches a daily_wide row 1:1 (same breakdown source, looser
    -- filter), so the LEFT JOIN never drops or fans out rows.
    left join platform_split_basis u
        on w.league_key = u.league_key
        and w.season_year = u.season_year
        and w.scoring_period = u.scoring_period
        and w.team_id is not distinct from u.team_id
        and w.player_id = u.player_id
        and w.lineup_slot = u.lineup_slot
)

select * from final

union all

-- The CBS branch (MLB-72): day-grain CBS production in the same column
-- contract. Explicit list so a drift in either file fails loudly at parse
-- rather than silently mis-mapping a positional UNION.
select
    league_key, season_year, matchup_period, scoring_period, team_id,
    team_name, team_abbrev, owner_name, player_id, player_name, display_name,
    position, pro_team, eligible_slots, lineup_slot, lineup_slot_category,
    is_active_slot, games_played, platform_points, platform_hitting_pts,
    platform_pitching_pts,
    h, ab, b_bb, b_so, hbp, sf, hr, r, rbi, sb, cs, tb, singles, doubles,
    triples, xbh, gdp, b_ibb, cyc,
    h_pts, ab_pts, b_bb_pts, b_so_pts, hbp_pts, sf_pts, hr_pts, r_pts,
    rbi_pts, sb_pts, cs_pts, tb_pts, singles_pts, doubles_pts, triples_pts,
    xbh_pts, gdp_pts, b_ibb_pts, cyc_pts,
    w, l, k, er, outs, qs, sv, hld, p_h, p_bb, p_hr, p_r, cg, blk, wp,
    hbp_p, blsv, nh, pg, pk, sho,
    w_pts, l_pts, k_pts, er_pts, outs_pts, qs_pts, sv_pts, hld_pts,
    p_h_pts, p_bb_pts, p_hr_pts, p_r_pts, cg_pts, blk_pts, wp_pts,
    hbp_p_pts, blsv_pts, nh_pts, pg_pts, pk_pts, sho_pts,
    total_hitting_stat_pts, total_pitching_stat_pts, total_stat_pts,
    negative_points,
    player_key, game_date, active_weight, provenance
from {{ ref('int_cbs__player_daily') }}
