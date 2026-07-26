-- mart_player_fpts_reconciliation.sql
-- The MLB-62 delta report: season-summed calculated_ FPTS (universal stats
-- priced by current CBS rules, int_cbs__player_game_points) against the
-- platform_ anchor (CBS's own awarded season FPTS, stg_cbs__player_season_
-- stats). Reconciliation, NOT exact-match -- the delta is a feature: it
-- surfaces era-rule changes and platform data gaps (the spike's exhibit:
-- Kirby Yates 2019, where CBS's feed carries no IRSTR so calculated_ is
-- MORE accurate than the platform's own total).
--
-- Grain: one row per (league_key, season_year, cbs_player_id, universe).
-- universe = the CBS season-universe the platform row came from ('hitting'
-- from the hitter file, 'pitching' from position=P), matched to the
-- calculated side's stat_group -- so a pre-2022 pitcher reconciles on his
-- pitching line only; his batting production (which CBS never priced) stays
-- out of the comparison by construction.
--
-- Population: the INNER join of both lenses -- players present in the CBS
-- season archive (free-agent-only, 2004-2025) AND crosswalked to MLBAM ids.
-- Currently-rostered players (Cole, Judge...) have NO platform anchor --
-- that absence is the whole universal-stats pivot -- and the Ohtani
-- sentinels (900/901) are absent from the season universes; neither
-- appears here. The record book does NOT read this mart; it reads the
-- calculated_ lens directly. This mart is the QA gate and the almanac's
-- asterisk factory.
--
-- Three diagnostics per row:
--   - fpts_delta = calculated - platform: the headline divergence.
--   - platform_identity_residual = platform_fpts - sum(platform's own stat
--     values x CURRENT weights): ~0 means CBS's total is self-consistent
--     under today's rules (the 587/587 anchor property) and the fpts_delta
--     is driven by STAT-VALUE differences (feed gaps); a large residual
--     means the platform total itself was computed under different rules
--     (era-rule detection).
--   - drivers: per-category {calculated, platform, pts_delta} object,
--     categories with pts_delta = 0 omitted -- which stats carry the
--     divergence (IRSTR-missing shows up here instantly).

with weights as (
    select league_key, cbs_key, stat_name, stat_category, points_per_unit
    from {{ ref('stg_cbs__scoring_settings') }}
),

-- Season sums of the calculated_ lens, per discipline-line.
calc as (
    select
        league_key,
        season_year,
        cbs_player_id,
        max(cbs_player_name) as cbs_player_name,
        stat_group,
        count(*)             as games,
        sum(r)     as r,     sum(rbi)   as rbi,   sum(bb)    as bb,
        sum(sb)    as sb,    sum(tb)    as tb,
        sum(w)     as w,     sum(s)     as s,     sum(hd)    as hd,
        sum(cg)    as cg,    sum(qs)    as qs,    sum(outs)  as outs,
        sum(irstr) as irstr, sum(k)     as k,     sum(ha)    as ha,
        sum(bbi)   as bbi,   sum(er)    as er,
        {{ stable_sum("r_pts", none) }}     as r_pts,     {{ stable_sum("rbi_pts", none) }}   as rbi_pts,
        {{ stable_sum("bb_pts", none) }}    as bb_pts,    {{ stable_sum("sb_pts", none) }}    as sb_pts,
        {{ stable_sum("tb_pts", none) }}    as tb_pts,    {{ stable_sum("w_pts", none) }}     as w_pts,
        {{ stable_sum("s_pts", none) }}     as s_pts,     {{ stable_sum("hd_pts", none) }}    as hd_pts,
        {{ stable_sum("cg_pts", none) }}    as cg_pts,    {{ stable_sum("qs_pts", none) }}    as qs_pts,
        {{ stable_sum("outs_pts", none) }}  as outs_pts,  {{ stable_sum("irstr_pts", none) }} as irstr_pts,
        {{ stable_sum("k_pts", none) }}     as k_pts,     {{ stable_sum("ha_pts", none) }}    as ha_pts,
        {{ stable_sum("bbi_pts", none) }}   as bbi_pts,   {{ stable_sum("er_pts", none) }}    as er_pts,
        {{ stable_sum("calculated_fpts", none) }} as calculated_fpts
    from {{ ref('int_cbs__player_game_points') }}
    group by league_key, season_year, cbs_player_id, stat_group
),

-- The platform anchor, pivoted from the long season rows. The weights join
-- translates ESPN-namespace stat_names back to CBS category keys (single-
-- sourced bridge -- no vocabulary mapping lives in this mart) and prices
-- the platform's own stat values under CURRENT rules for the identity
-- residual. PLATFORM_POINTS (unpriced, not a category) pivots directly.
plat as (
    select
        p.league_key,
        p.season_year,
        p.player_id                as cbs_player_id,
        max(p.player_name)         as player_name,
        p.universe,
        max(case when p.stat_name = 'PLATFORM_POINTS' then p.stat_value end) as platform_fpts,
        sum(case when w.cbs_key is not null then p.stat_value * w.points_per_unit else 0 end)
            as platform_recomputed_fpts,
        max(case when w.cbs_key = 'R'     then p.stat_value end) as r,
        max(case when w.cbs_key = 'RBI'   then p.stat_value end) as rbi,
        max(case when w.cbs_key = 'BB'    then p.stat_value end) as bb,
        max(case when w.cbs_key = 'SB'    then p.stat_value end) as sb,
        max(case when w.cbs_key = 'TB'    then p.stat_value end) as tb,
        max(case when w.cbs_key = 'W'     then p.stat_value end) as w,
        max(case when w.cbs_key = 'S'     then p.stat_value end) as s,
        max(case when w.cbs_key = 'HD'    then p.stat_value end) as hd,
        max(case when w.cbs_key = 'CG'    then p.stat_value end) as cg,
        max(case when w.cbs_key = 'QS'    then p.stat_value end) as qs,
        max(case when w.cbs_key = 'INN'   then p.stat_value end) as outs,
        max(case when w.cbs_key = 'IRSTR' then p.stat_value end) as irstr,
        max(case when w.cbs_key = 'K'     then p.stat_value end) as k,
        max(case when w.cbs_key = 'HA'    then p.stat_value end) as ha,
        max(case when w.cbs_key = 'BBI'   then p.stat_value end) as bbi,
        max(case when w.cbs_key = 'ER'    then p.stat_value end) as er,
        max(case when w.cbs_key = 'R'     then p.stat_value * w.points_per_unit end) as r_pts,
        max(case when w.cbs_key = 'RBI'   then p.stat_value * w.points_per_unit end) as rbi_pts,
        max(case when w.cbs_key = 'BB'    then p.stat_value * w.points_per_unit end) as bb_pts,
        max(case when w.cbs_key = 'SB'    then p.stat_value * w.points_per_unit end) as sb_pts,
        max(case when w.cbs_key = 'TB'    then p.stat_value * w.points_per_unit end) as tb_pts,
        max(case when w.cbs_key = 'W'     then p.stat_value * w.points_per_unit end) as w_pts,
        max(case when w.cbs_key = 'S'     then p.stat_value * w.points_per_unit end) as s_pts,
        max(case when w.cbs_key = 'HD'    then p.stat_value * w.points_per_unit end) as hd_pts,
        max(case when w.cbs_key = 'CG'    then p.stat_value * w.points_per_unit end) as cg_pts,
        max(case when w.cbs_key = 'QS'    then p.stat_value * w.points_per_unit end) as qs_pts,
        max(case when w.cbs_key = 'INN'   then p.stat_value * w.points_per_unit end) as outs_pts,
        max(case when w.cbs_key = 'IRSTR' then p.stat_value * w.points_per_unit end) as irstr_pts,
        max(case when w.cbs_key = 'K'     then p.stat_value * w.points_per_unit end) as k_pts,
        max(case when w.cbs_key = 'HA'    then p.stat_value * w.points_per_unit end) as ha_pts,
        max(case when w.cbs_key = 'BBI'   then p.stat_value * w.points_per_unit end) as bbi_pts,
        max(case when w.cbs_key = 'ER'    then p.stat_value * w.points_per_unit end) as er_pts
    from {{ ref('stg_cbs__player_season_stats') }} p
    left join weights w
        on p.league_key = w.league_key
        and p.stat_name = w.stat_name
        -- Universe-scoped, mirroring the calculated side's per-group stat
        -- map: batting categories price hitter-universe rows only, pitching
        -- categories pitcher-universe rows only. Guards against any
        -- cross-discipline key a universe file might carry.
        and p.universe = w.stat_category
    group by p.league_key, p.season_year, p.player_id, p.universe
)

select
    c.league_key,
    c.season_year,
    c.cbs_player_id,
    coalesce(p.player_name, c.cbs_player_name) as player_name,
    p.universe,
    c.games,
    c.calculated_fpts,
    p.platform_fpts,
    c.calculated_fpts - p.platform_fpts        as fpts_delta,
    abs(c.calculated_fpts - p.platform_fpts)   as abs_fpts_delta,
    (c.calculated_fpts - p.platform_fpts) / nullif(abs(p.platform_fpts), 0)
                                               as fpts_delta_pct,
    p.platform_recomputed_fpts,
    p.platform_fpts - p.platform_recomputed_fpts as platform_identity_residual,
    -- Per-category divergence, zero-delta categories omitted (a NULL value
    -- drops its key from OBJECT_CONSTRUCT). Platform NULLs (untracked
    -- categories -- early-year IRSTR) are compared as 0: exactly the gap
    -- the report exists to surface.
    object_construct(
        'R',     iff(c.r_pts     - coalesce(p.r_pts, 0)     = 0, null, object_construct('calculated', c.r,     'platform', p.r,     'pts_delta', c.r_pts     - coalesce(p.r_pts, 0))),
        'RBI',   iff(c.rbi_pts   - coalesce(p.rbi_pts, 0)   = 0, null, object_construct('calculated', c.rbi,   'platform', p.rbi,   'pts_delta', c.rbi_pts   - coalesce(p.rbi_pts, 0))),
        'BB',    iff(c.bb_pts    - coalesce(p.bb_pts, 0)    = 0, null, object_construct('calculated', c.bb,    'platform', p.bb,    'pts_delta', c.bb_pts    - coalesce(p.bb_pts, 0))),
        'SB',    iff(c.sb_pts    - coalesce(p.sb_pts, 0)    = 0, null, object_construct('calculated', c.sb,    'platform', p.sb,    'pts_delta', c.sb_pts    - coalesce(p.sb_pts, 0))),
        'TB',    iff(c.tb_pts    - coalesce(p.tb_pts, 0)    = 0, null, object_construct('calculated', c.tb,    'platform', p.tb,    'pts_delta', c.tb_pts    - coalesce(p.tb_pts, 0))),
        'W',     iff(c.w_pts     - coalesce(p.w_pts, 0)     = 0, null, object_construct('calculated', c.w,     'platform', p.w,     'pts_delta', c.w_pts     - coalesce(p.w_pts, 0))),
        'S',     iff(c.s_pts     - coalesce(p.s_pts, 0)     = 0, null, object_construct('calculated', c.s,     'platform', p.s,     'pts_delta', c.s_pts     - coalesce(p.s_pts, 0))),
        'HD',    iff(c.hd_pts    - coalesce(p.hd_pts, 0)    = 0, null, object_construct('calculated', c.hd,    'platform', p.hd,    'pts_delta', c.hd_pts    - coalesce(p.hd_pts, 0))),
        'CG',    iff(c.cg_pts    - coalesce(p.cg_pts, 0)    = 0, null, object_construct('calculated', c.cg,    'platform', p.cg,    'pts_delta', c.cg_pts    - coalesce(p.cg_pts, 0))),
        'QS',    iff(c.qs_pts    - coalesce(p.qs_pts, 0)    = 0, null, object_construct('calculated', c.qs,    'platform', p.qs,    'pts_delta', c.qs_pts    - coalesce(p.qs_pts, 0))),
        'INN',   iff(c.outs_pts  - coalesce(p.outs_pts, 0)  = 0, null, object_construct('calculated', c.outs,  'platform', p.outs,  'pts_delta', c.outs_pts  - coalesce(p.outs_pts, 0))),
        'IRSTR', iff(c.irstr_pts - coalesce(p.irstr_pts, 0) = 0, null, object_construct('calculated', c.irstr, 'platform', p.irstr, 'pts_delta', c.irstr_pts - coalesce(p.irstr_pts, 0))),
        'K',     iff(c.k_pts     - coalesce(p.k_pts, 0)     = 0, null, object_construct('calculated', c.k,     'platform', p.k,     'pts_delta', c.k_pts     - coalesce(p.k_pts, 0))),
        'HA',    iff(c.ha_pts    - coalesce(p.ha_pts, 0)    = 0, null, object_construct('calculated', c.ha,    'platform', p.ha,    'pts_delta', c.ha_pts    - coalesce(p.ha_pts, 0))),
        'BBI',   iff(c.bbi_pts   - coalesce(p.bbi_pts, 0)   = 0, null, object_construct('calculated', c.bbi,   'platform', p.bbi,   'pts_delta', c.bbi_pts   - coalesce(p.bbi_pts, 0))),
        'ER',    iff(c.er_pts    - coalesce(p.er_pts, 0)    = 0, null, object_construct('calculated', c.er,    'platform', p.er,    'pts_delta', c.er_pts    - coalesce(p.er_pts, 0)))
    ) as drivers
from calc c
inner join plat p
    on c.league_key = p.league_key
    and c.season_year = p.season_year
    and c.cbs_player_id = p.cbs_player_id
    and c.stat_group = p.universe
