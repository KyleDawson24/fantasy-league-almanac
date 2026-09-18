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
--   game_date     the real calendar date. CBS serves it; ESPN does not, so
--                 the ESPN branch derives it from the season opener plus its
--                 scoring_period ordinal (MLB-263). Both branches now fill
--                 it, so the contract has ONE date vocabulary.
--   active_weight the day's scoring weight: ESPN 1/0 by slot; CBS 1/0 where
--                 the state is known, the start-share estimator (or NULL)
--                 where 2004-2020 history is estimated.
--   provenance    how we know the day's state ('captured' for everything
--                 platform-served; the walk-back enum for CBS history).
--
-- MLB-295 / MLB-284 defect #2: both union branches explicitly cast every
-- column to its semantic type. Counting stats, periods and numeric team/player
-- ids are INTEGER (proven lossless); points and weights are DOUBLE. The
-- platform-neutral player_key remains VARCHAR. Eligibility uses one JSON-array
-- representation on DuckDB and ARRAY on Snowflake.
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
        cast(w.league_key as varchar) as league_key,
        cast(w.season_year as integer) as season_year,
        cast(w.matchup_period as integer) as matchup_period,
        cast(w.scoring_period as integer) as scoring_period,
        cast(w.team_id as integer) as team_id,
        cast(w.team_name as varchar) as team_name,
        cast(w.team_abbrev as varchar) as team_abbrev,
        cast(w.owner_name as varchar) as owner_name,
        cast(w.player_id as integer) as player_id,
        cast(w.player_name as varchar) as player_name,
        cast(coalesce(b.display_name, w.player_name) as varchar) as display_name,
        cast(b.position as varchar) as position,
        cast(b.pro_team as varchar) as pro_team,
        -- The MLB Stats API id behind that spelling (MLB-263, S-41):
        -- ESPN writes `Ari` / `ChC` / `Wsh`, the seed lists the
        -- upper-case spellings, and the join is case-folded. NULL only
        -- when pro_team is NULL (no appearance) or the seed does not
        -- know the spelling -- and the singular test
        -- assert_espn_pro_team_resolves_to_mlb_team makes the second
        -- case loud rather than silent.
        cast(ab.team_id as integer) as mlb_team_id,
        {{ to_json_array('b.eligible_slots') }} as eligible_slots,
        cast(w.lineup_slot as varchar) as lineup_slot,
        cast(w.lineup_slot_category as varchar) as lineup_slot_category,
        cast(case when w.lineup_slot_category != 'inactive' then true else false end as boolean) as is_active_slot,
        cast(b.games_played as integer) as games_played,
        cast(b.points as double) as platform_points,

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
        cast(case
            when coalesce(u.unfiltered_hitting_pts, 0)
               + coalesce(u.unfiltered_pitching_pts, 0) = 0
                then case when w.lineup_slot in ('SP', 'RP', 'P') then 0 else b.points end
            else b.points * u.unfiltered_hitting_pts
                 / (u.unfiltered_hitting_pts + u.unfiltered_pitching_pts)
        end as double) as platform_hitting_pts,
        cast(case
            when coalesce(u.unfiltered_hitting_pts, 0)
               + coalesce(u.unfiltered_pitching_pts, 0) = 0
                then case when w.lineup_slot in ('SP', 'RP', 'P') then b.points else 0 end
            else b.points * u.unfiltered_pitching_pts
                 / (u.unfiltered_hitting_pts + u.unfiltered_pitching_pts)
        end as double) as platform_pitching_pts,

        -- Wide stats (passthrough)
        cast(w.h as integer) as h,
        cast(w.ab as integer) as ab,
        cast(w.b_bb as integer) as b_bb,
        cast(w.b_so as integer) as b_so,
        cast(w.hbp as integer) as hbp,
        cast(w.sf as integer) as sf,
        cast(w.hr as integer) as hr,
        cast(w.r as integer) as r,
        cast(w.rbi as integer) as rbi,
        cast(w.sb as integer) as sb,
        cast(w.cs as integer) as cs,
        cast(w.tb as integer) as tb,
        cast(w.singles as integer) as singles,
        cast(w.doubles as integer) as doubles,
        cast(w.triples as integer) as triples,
        cast(w.xbh as integer) as xbh,
        cast(w.gdp as integer) as gdp,
        cast(w.b_ibb as integer) as b_ibb,
        cast(w.cyc as integer) as cyc,
        cast(w.h_pts as double) as h_pts,
        cast(w.ab_pts as double) as ab_pts,
        cast(w.b_bb_pts as double) as b_bb_pts,
        cast(w.b_so_pts as double) as b_so_pts,
        cast(w.hbp_pts as double) as hbp_pts,
        cast(w.sf_pts as double) as sf_pts,
        cast(w.hr_pts as double) as hr_pts,
        cast(w.r_pts as double) as r_pts,
        cast(w.rbi_pts as double) as rbi_pts,
        cast(w.sb_pts as double) as sb_pts,
        cast(w.cs_pts as double) as cs_pts,
        cast(w.tb_pts as double) as tb_pts,
        cast(w.singles_pts as double) as singles_pts,
        cast(w.doubles_pts as double) as doubles_pts,
        cast(w.triples_pts as double) as triples_pts,
        cast(w.xbh_pts as double) as xbh_pts,
        cast(w.gdp_pts as double) as gdp_pts,
        cast(w.b_ibb_pts as double) as b_ibb_pts,
        cast(w.cyc_pts as double) as cyc_pts,
        cast(w.w as integer) as w,
        cast(w.l as integer) as l,
        cast(w.k as integer) as k,
        cast(w.er as integer) as er,
        cast(w.outs as integer) as outs,
        cast(w.qs as integer) as qs,
        cast(w.sv as integer) as sv,
        cast(w.hld as integer) as hld,
        cast(w.p_h as integer) as p_h,
        cast(w.p_bb as integer) as p_bb,
        cast(w.p_hr as integer) as p_hr,
        cast(w.p_r as integer) as p_r,
        cast(w.cg as integer) as cg,
        cast(w.blk as integer) as blk,
        cast(w.wp as integer) as wp,
        cast(w.hbp_p as integer) as hbp_p,
        cast(w.blsv as integer) as blsv,
        cast(w.nh as integer) as nh,
        cast(w.pg as integer) as pg,
        cast(w.pk as integer) as pk,
        cast(w.sho as integer) as sho,
        cast(w.w_pts as double) as w_pts,
        cast(w.l_pts as double) as l_pts,
        cast(w.k_pts as double) as k_pts,
        cast(w.er_pts as double) as er_pts,
        cast(w.outs_pts as double) as outs_pts,
        cast(w.qs_pts as double) as qs_pts,
        cast(w.sv_pts as double) as sv_pts,
        cast(w.hld_pts as double) as hld_pts,
        cast(w.p_h_pts as double) as p_h_pts,
        cast(w.p_bb_pts as double) as p_bb_pts,
        cast(w.p_hr_pts as double) as p_hr_pts,
        cast(w.p_r_pts as double) as p_r_pts,
        cast(w.cg_pts as double) as cg_pts,
        cast(w.blk_pts as double) as blk_pts,
        cast(w.wp_pts as double) as wp_pts,
        cast(w.hbp_p_pts as double) as hbp_p_pts,
        cast(w.blsv_pts as double) as blsv_pts,
        cast(w.nh_pts as double) as nh_pts,
        cast(w.pg_pts as double) as pg_pts,
        cast(w.pk_pts as double) as pk_pts,
        cast(w.sho_pts as double) as sho_pts,

        cast(w.total_hitting_stat_pts as double) as total_hitting_stat_pts,
        cast(w.total_pitching_stat_pts as double) as total_pitching_stat_pts,
        cast(w.total_stat_pts as double) as total_stat_pts,

        -- Per-day net-negative platform_points magnitude. Stored UNSIGNED
        -- (positive) so the leaderboard ranks "most damage" naturally with
        -- ORDER BY DESC. Aggregates via SUM at the weekly layer: for a
        -- player with one -5 day and net-positive week, the weekly rollup
        -- = 5 (the -5 magnitude). Matches the existing doubly_wasted_pts
        -- concept (consumer-side: GREATEST(0, -week_active_platform_pts))
        -- when all the player's negative days fall in active slots, and is
        -- finer-grained otherwise (separately attributes each negative day).
        cast(case when b.points < 0 then -b.points else 0 end as double) as negative_points,

        -- Union-layer columns (see the header). ESPN state is always
        -- platform-served, hence 'captured' / binary weight.
        cast({{ to_varchar('w.player_id') }} as varchar) as player_key,
        -- ONE TIME SPINE (MLB-263, ledger S-36). ESPN serves no ISO date --
        -- its day identity is the scoring_period ordinal -- so this column
        -- was NULL on every ESPN row and `espn_points_data` reconstructed
        -- the calendar in Python (season_context/period_to_date/
        -- month_window) from exactly the rule applied here. Deriving it once
        -- in the contract means the daily fact has a single date vocabulary
        -- and no renderer has to rebuild one.
        --
        -- The anchor is MLB's own published regular-season start, not a
        -- typed value, and it joins on season_year ALONE: the season starts
        -- when it starts, so it is not league-scoped -- the same reasoning
        -- dim_matchup_period's derived_dates uses. stg_mlb__season_calendar
        -- is one row per season, so this cannot fan out.
        --
        -- WRAPPED IN to_date_of FOR THE SAME REASON dim_matchup_period wraps
        -- its arithmetic: Snowflake's DATEADD(day, n, <date>) returns a DATE
        -- and DuckDB's `<date> + to_days(n)` returns a TIMESTAMP, and left
        -- alone the two engines would disagree silently -- CBS rows carry a
        -- real DATE here, so an unwrapped ESPN branch would also make the
        -- union's own column type engine-dependent.
        -- The day offset is CAST TO INTEGER, and that cast is load-bearing
        -- rather than decorative. scoring_period is a NUMBER, so
        -- `scoring_period - 1` is DECIMAL(38,0); Snowflake's DATEADD accepts
        -- it, but DuckDB expands date_add_unit to `<date> + to_days(n)` and
        -- to_days() takes an INTEGER, failing with a Binder Error. The twin
        -- derivation in dim_matchup_period never hit this only because its
        -- evidence model had already cast its bounds to integer. Measured on
        -- the DuckDB lane (tests/test_weekly_chain_without_seed.py), not
        -- theorised.
        cast({{ to_date_of(date_add_unit('day',
                                    'cast(w.scoring_period as integer) - 1',
                                    'cal.season_opener')) }} as date) as game_date,
        cast({{ iff("w.lineup_slot_category != 'inactive'", '1.0', '0.0') }} as double) as active_weight,
        cast('captured' as varchar) as provenance
    from daily_wide w
    -- The season anchor for game_date above. LEFT so a season with no
    -- captured calendar row yields NULL rather than dropping the day.
    left join {{ ref('stg_mlb__season_calendar') }} cal
        on w.season_year = cal.season_year
    left join {{ ref('stg_box_scores') }} b
        on w.league_key = b.league_key
        and w.season_year = b.season_year
        and w.scoring_period = b.scoring_period
        and w.team_id is not distinct from b.team_id
        and w.player_id = b.player_id
        and w.lineup_slot = b.lineup_slot
    -- Club spelling -> MLB Stats API id. The seed's spellings are unique
    -- (one id per spelling), so this cannot fan out.
    left join {{ ref('mlb_team_abbrevs') }} ab
        on ab.cbs_abbrev = upper(b.pro_team)
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
        cast(league_key as varchar) as league_key,
        cast(season_year as integer) as season_year,
        cast(matchup_period as integer) as matchup_period,
        cast(scoring_period as integer) as scoring_period,
        cast(team_id as integer) as team_id,
        cast(team_name as varchar) as team_name,
        cast(team_abbrev as varchar) as team_abbrev,
        cast(owner_name as varchar) as owner_name,
        cast(player_id as integer) as player_id,
        cast(player_name as varchar) as player_name,
        cast(display_name as varchar) as display_name,
        cast(position as varchar) as position,
        cast(pro_team as varchar) as pro_team,
        cast(mlb_team_id as integer) as mlb_team_id,
        {{ to_json_array('eligible_slots') }} as eligible_slots,
        cast(lineup_slot as varchar) as lineup_slot,
        cast(lineup_slot_category as varchar) as lineup_slot_category,
        cast(is_active_slot as boolean) as is_active_slot,
        cast(games_played as integer) as games_played,
        cast(platform_points as double) as platform_points,
        cast(platform_hitting_pts as double) as platform_hitting_pts,
        cast(platform_pitching_pts as double) as platform_pitching_pts,
        cast(h as integer) as h,
        cast(ab as integer) as ab,
        cast(b_bb as integer) as b_bb,
        cast(b_so as integer) as b_so,
        cast(hbp as integer) as hbp,
        cast(sf as integer) as sf,
        cast(hr as integer) as hr,
        cast(r as integer) as r,
        cast(rbi as integer) as rbi,
        cast(sb as integer) as sb,
        cast(cs as integer) as cs,
        cast(tb as integer) as tb,
        cast(singles as integer) as singles,
        cast(doubles as integer) as doubles,
        cast(triples as integer) as triples,
        cast(xbh as integer) as xbh,
        cast(gdp as integer) as gdp,
        cast(b_ibb as integer) as b_ibb,
        cast(cyc as integer) as cyc,
        cast(h_pts as double) as h_pts,
        cast(ab_pts as double) as ab_pts,
        cast(b_bb_pts as double) as b_bb_pts,
        cast(b_so_pts as double) as b_so_pts,
        cast(hbp_pts as double) as hbp_pts,
        cast(sf_pts as double) as sf_pts,
        cast(hr_pts as double) as hr_pts,
        cast(r_pts as double) as r_pts,
        cast(rbi_pts as double) as rbi_pts,
        cast(sb_pts as double) as sb_pts,
        cast(cs_pts as double) as cs_pts,
        cast(tb_pts as double) as tb_pts,
        cast(singles_pts as double) as singles_pts,
        cast(doubles_pts as double) as doubles_pts,
        cast(triples_pts as double) as triples_pts,
        cast(xbh_pts as double) as xbh_pts,
        cast(gdp_pts as double) as gdp_pts,
        cast(b_ibb_pts as double) as b_ibb_pts,
        cast(cyc_pts as double) as cyc_pts,
        cast(w as integer) as w,
        cast(l as integer) as l,
        cast(k as integer) as k,
        cast(er as integer) as er,
        cast(outs as integer) as outs,
        cast(qs as integer) as qs,
        cast(sv as integer) as sv,
        cast(hld as integer) as hld,
        cast(p_h as integer) as p_h,
        cast(p_bb as integer) as p_bb,
        cast(p_hr as integer) as p_hr,
        cast(p_r as integer) as p_r,
        cast(cg as integer) as cg,
        cast(blk as integer) as blk,
        cast(wp as integer) as wp,
        cast(hbp_p as integer) as hbp_p,
        cast(blsv as integer) as blsv,
        cast(nh as integer) as nh,
        cast(pg as integer) as pg,
        cast(pk as integer) as pk,
        cast(sho as integer) as sho,
        cast(w_pts as double) as w_pts,
        cast(l_pts as double) as l_pts,
        cast(k_pts as double) as k_pts,
        cast(er_pts as double) as er_pts,
        cast(outs_pts as double) as outs_pts,
        cast(qs_pts as double) as qs_pts,
        cast(sv_pts as double) as sv_pts,
        cast(hld_pts as double) as hld_pts,
        cast(p_h_pts as double) as p_h_pts,
        cast(p_bb_pts as double) as p_bb_pts,
        cast(p_hr_pts as double) as p_hr_pts,
        cast(p_r_pts as double) as p_r_pts,
        cast(cg_pts as double) as cg_pts,
        cast(blk_pts as double) as blk_pts,
        cast(wp_pts as double) as wp_pts,
        cast(hbp_p_pts as double) as hbp_p_pts,
        cast(blsv_pts as double) as blsv_pts,
        cast(nh_pts as double) as nh_pts,
        cast(pg_pts as double) as pg_pts,
        cast(pk_pts as double) as pk_pts,
        cast(sho_pts as double) as sho_pts,
        cast(total_hitting_stat_pts as double) as total_hitting_stat_pts,
        cast(total_pitching_stat_pts as double) as total_pitching_stat_pts,
        cast(total_stat_pts as double) as total_stat_pts,
        cast(negative_points as double) as negative_points,
        cast(player_key as varchar) as player_key,
        cast(game_date as date) as game_date,
        cast(active_weight as double) as active_weight,
        cast(provenance as varchar) as provenance
from {{ ref('int_cbs__player_daily') }}
