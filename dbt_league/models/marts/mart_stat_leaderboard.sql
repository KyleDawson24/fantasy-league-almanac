-- mart_stat_leaderboard.sql
-- Top-10 leaderboard across team AND player grains, for both stat-level
-- (HR, K, RBI, etc.) and score-level columns. Both platform_* (ESPN's
-- official tally at the time) and calculated_* (rules-normalized under
-- current weights) score columns are included so consumers can choose
-- which lens to rank by. Phase 5 surfaces calculated_* in the records
-- output; platform_* remains available for cross-season comparisons.
--
-- Phase 5 (#3): added `record_direction` dimension. The mart emits both
-- top-N (rank by stat_value DESC) and bottom-N (ASC) rankings.
-- Phase 6.3.3: renamed direction values 'best'/'worst' -> 'most'/'fewest'
-- (the mart is direction-agnostic; consumer-side polarity filtering decides
-- which direction is "best" vs "worst" for any given stat -- e.g., 'most'
-- is best for HR, worst for ER allowed). Cap held at top-10 per direction
-- as a buffer for the consumer-side tie-collapse logic: top-5 is the
-- display target, but the collapse rule needs to see tiers extending past
-- rank 5 to decide when to render "N teams tied at value" instead of
-- listing each entry individually. count_value_occurrences() in records.py
-- backstops collapsed tiers with accurate totals when the leaderboard's
-- top-10 saturates at one value (e.g., the SV/W zero-floor pattern).
--
-- Implementation uses Snowflake UNPIVOT to fold wide columns from
-- fct_weekly_team_active_performance and fct_weekly_player_active_performance back into
-- (stat_name, stat_value) long format, then ranks uniformly. UNPIVOT is
-- Snowflake-specific; if the project ever moves to a different warehouse
-- (e.g. DuckDB for a local-CLI build), this can be rewritten as an explicit
-- UNION ALL per stat column -- tedious but portable.
--
-- Grain: (entity_grain, stat_name, record_scope, record_direction, rank).
-- entity_grain in {'team', 'player'}. record_scope in {'all_time',
-- 'current_season'}. record_direction in {'most', 'fewest'}. Rank 1..5
-- per (entity_grain, stat_name, record_scope, record_direction).
--
-- Excludes abnormal matchup periods via matchup_schedule.is_abnormal = false.
-- Ties broken by recency (newer season_year, then newer matchup_period) in
-- BOTH directions -- so a team that just tied a record is rank 1 either way.
-- View materialization -- rankings are retroactively mutable so incremental
-- would be fragile. Zero storage, always fresh.

{{ config(materialized='view') }}

-- Phase 6.3.3 chunk 2: team-grain wasted-points sum, used as a derived
-- counting column in the team source CTE below. Filtered to the
-- ROSTERED_INACTIVE bucket so the leaderboard reflects "bench/IL waste"
-- only -- FAs (mart_wasted_points's other bucket) have no team_id and
-- are nobody's roster decision, so they don't belong in a team-versus-
-- team comparison. Aggregated per (season_year, matchup_period, team_id)
-- because mart_wasted_points's grain is per-player-per-week.
with team_wasted as (
    select
        season_year,
        matchup_period,
        team_id,
        sum(wasted_points) as wasted_points
    from {{ ref('mart_wasted_points') }}
    where wasted_bucket = 'ROSTERED_INACTIVE'
    group by 1, 2, 3
),

team_source as (
    -- Phase 6.3.3 chunk 2: derived stats (PA, SB-CS, W-L, SV-BLSV) and
    -- existing rate stats (ERA/WHIP/K/9/K/BB) joined into the leaderboard
    -- universe; team-grain only adds wasted_points via team_wasted above.
    select
        t.season_year,
        t.matchup_period,
        t.team_id,
        t.team_name,
        t.team_abbrev,
        t.owner_name,
        t.h, t.ab, t.b_bb, t.b_so, t.hbp, t.sf, t.hr, t.r, t.rbi,
        t.sb, t.cs, t.tb, t.singles, t.doubles, t.triples, t.xbh,
        t.gdp, t.b_ibb,
        t.w, t.l, t.k, t.er, t.outs, t.qs, t.sv, t.hld,
        t.p_h, t.p_bb, t.p_hr, t.p_r, t.cg, t.blk, t.wp,
        t.hbp_p, t.blsv, t.nh, t.pg, t.pk, t.sho,

        -- Derived counting stats (computed inline; no fact-layer column).
        -- Phase 6.3.3 chunk 2: surface combined / net stats that the
        -- league cares about as records but that aren't a single ESPN
        -- stat. PA matches the seed's id-16 definition (AB + walks +
        -- HBP + SF). Net stats expose efficiency stories: SB-CS, W-L
        -- (record), SV-BLSV (effective save rate). Computed at the
        -- mart layer rather than the fact because they're consumer-
        -- facing analytical metrics; the facts stay close to raw API
        -- semantics.
        t.ab + t.b_bb + t.hbp + t.sf as pa,
        t.sb - t.cs                  as sb_cs,
        t.w  - t.l                   as w_l,
        t.sv - t.blsv                as sv_blsv,

        -- Existing rate stats from the fact, surfaced into the leaderboard.
        -- ERA / WHIP / K/9 / K/BB live on fct_weekly_team_active_performance via
        -- the rate-stat macros; the mart just needed to pull and unpivot.
        t.era, t.whip, t.k_per_9, t.k_per_bb,

        -- HR/9 and BB/9 don't have macros yet; computed inline here. 27.0
        -- factor = 9 innings * 3 outs/inning. Outs guard prevents div/0
        -- on weeks where a team had no innings pitched -- NULL is dropped
        -- by Snowflake UNPIVOT's default EXCLUDE NULLS, so those rows
        -- naturally don't appear in the leaderboard for these stats.
        case when t.outs > 0 then t.p_hr * 27.0 / t.outs else null end as hr_per_9,
        case when t.outs > 0 then t.p_bb * 27.0 / t.outs else null end as bb_per_9,

        -- Wasted points (team grain only -- no wasted_points column on
        -- the player source CTE). LEFT JOIN: a team-week with zero
        -- bench/IL waste produces no row in team_wasted, so wasted_points
        -- is NULL there. UNPIVOT EXCLUDE NULLS drops those rows from the
        -- leaderboard, which is correct behavior (a team that wasted
        -- nothing shouldn't appear on a wasted-points leaderboard).
        tw.wasted_points,

        t.platform_points, t.platform_hitting_pts, t.platform_pitching_pts,
        t.calculated_points, t.calculated_hitting_pts, t.calculated_pitching_pts
    from {{ ref('fct_weekly_team_active_performance') }} t
    inner join {{ ref('matchup_schedule') }} s
        on t.season_year = s.season_year
        and t.matchup_period = s.matchup_period
    left join team_wasted tw
        on t.season_year = tw.season_year
        and t.matchup_period = tw.matchup_period
        and t.team_id = tw.team_id
    where s.is_abnormal = false
),

team_unpivoted as (
    select
        'team'::varchar                as entity_grain,
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        null::integer                  as player_id,
        null::varchar                  as player_name,
        null::varchar                  as display_name,
        stat_name,
        stat_value
    from team_source
    unpivot (stat_value for stat_name in (
        h, ab, b_bb, b_so, hbp, sf, hr, r, rbi,
        sb, cs, tb, singles, doubles, triples, xbh,
        gdp, b_ibb,
        w, l, k, er, outs, qs, sv, hld,
        p_h, p_bb, p_hr, p_r, cg, blk, wp,
        hbp_p, blsv, nh, pg, pk, sho,
        pa, sb_cs, w_l, sv_blsv,
        era, whip, k_per_9, k_per_bb, hr_per_9, bb_per_9,
        wasted_points,
        platform_points, platform_hitting_pts, platform_pitching_pts,
        calculated_points, calculated_hitting_pts, calculated_pitching_pts
    ))
),

player_source as (
    select
        p.season_year,
        p.matchup_period,
        p.team_id,
        p.team_name,
        p.team_abbrev,
        p.owner_name,
        p.player_id,
        p.player_name,
        p.display_name,
        p.h, p.ab, p.b_bb, p.b_so, p.hbp, p.sf, p.hr, p.r, p.rbi,
        p.sb, p.cs, p.tb, p.singles, p.doubles, p.triples, p.xbh,
        p.gdp, p.b_ibb,
        p.w, p.l, p.k, p.er, p.outs, p.qs, p.sv, p.hld,
        p.p_h, p.p_bb, p.p_hr, p.p_r, p.cg, p.blk, p.wp,
        p.hbp_p, p.blsv, p.nh, p.pg, p.pk, p.sho,

        -- Derived counting stats (mirrors team_source). Same metric
        -- definitions so leaderboards across grains stay comparable. No
        -- wasted_points here -- waste is a roster-decision metric and
        -- not meaningful at player grain.
        --
        -- Rate stats (ERA / WHIP / K/9 / K/BB / HR/9 / BB/9) are
        -- intentionally NOT included at player grain. Single-IP relief
        -- appearances produce 27.00 WHIP / 243 ERA values that dominate
        -- the leaderboard but carry no signal -- and the league only
        -- cares about rate records at team grain anyway. Player-level
        -- rate stats remain available on fct_weekly_player_performance
        -- for ad-hoc analysis; they're just excluded from being treated
        -- as records here. Phase 6.3.3 chunk 3 decision (Path A): drop
        -- at mart layer rather than threshold-filter at output, so
        -- ad-hoc mart queries don't surface noise either.
        p.ab + p.b_bb + p.hbp + p.sf as pa,
        p.sb - p.cs                  as sb_cs,
        p.w  - p.l                   as w_l,
        p.sv - p.blsv                as sv_blsv,

        p.platform_points, p.platform_hitting_pts, p.platform_pitching_pts,
        p.calculated_points, p.calculated_hitting_pts, p.calculated_pitching_pts
    from {{ ref('fct_weekly_player_active_performance') }} p
    inner join {{ ref('matchup_schedule') }} s
        on p.season_year = s.season_year
        and p.matchup_period = s.matchup_period
    where s.is_abnormal = false
),

player_unpivoted as (
    select
        'player'::varchar  as entity_grain,
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        player_id,
        player_name,
        display_name,
        stat_name,
        stat_value
    from player_source
    unpivot (stat_value for stat_name in (
        h, ab, b_bb, b_so, hbp, sf, hr, r, rbi,
        sb, cs, tb, singles, doubles, triples, xbh,
        gdp, b_ibb,
        w, l, k, er, outs, qs, sv, hld,
        p_h, p_bb, p_hr, p_r, cg, blk, wp,
        hbp_p, blsv, nh, pg, pk, sho,
        pa, sb_cs, w_l, sv_blsv,
        platform_points, platform_hitting_pts, platform_pitching_pts,
        calculated_points, calculated_hitting_pts, calculated_pitching_pts
    ))
),

combined as (
    select * from team_unpivoted
    union all
    select * from player_unpivoted
),

current_year as (
    select max(season_year) as y from combined
),

-- Four rank dimensions: {all_time, current_season} x {most, fewest}.
-- Each computes top-5 in its direction; combined output has a
-- record_direction column distinguishing most from fewest.
--
-- Phase 7 B1: ORDER BY now ends in (team_id, player_id) for deterministic
-- ordering within true ties. Without it, Snowflake's view-evaluation order
-- for tied (stat_value, season_year, matchup_period) tuples is unspecified
-- and could swap which entity claims rank 1 on identical data across runs --
-- which makes the golden-output tests flaky. Pulled forward from sub-chunk F
-- (the mart rewrite) so B1's baseline pin is stable.

all_time_most as (
    select
        'all_time'::varchar as record_scope,
        'most'::varchar     as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value desc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from combined c
),

all_time_fewest as (
    select
        'all_time'::varchar as record_scope,
        'fewest'::varchar   as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value asc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from combined c
),

current_season_most as (
    select
        'current_season'::varchar as record_scope,
        'most'::varchar           as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value desc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from combined c
    where c.season_year = (select y from current_year)
),

current_season_fewest as (
    select
        'current_season'::varchar as record_scope,
        'fewest'::varchar         as record_direction,
        c.*,
        row_number() over (
            partition by entity_grain, stat_name
            order by stat_value asc, season_year desc, matchup_period desc, team_id, player_id
        ) as rank
    from combined c
    where c.season_year = (select y from current_year)
)

select * from all_time_most         where rank <= 10
union all
select * from all_time_fewest       where rank <= 10
union all
select * from current_season_most   where rank <= 10
union all
select * from current_season_fewest where rank <= 10
