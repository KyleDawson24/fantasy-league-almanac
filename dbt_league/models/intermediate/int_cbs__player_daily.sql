-- int_cbs__player_daily.sql
-- The MLB-72 union feed: CBS day-grain production in int_player_daily's
-- EXACT column contract, so the shared daily row serves both leagues ("the
-- point at which we produce an output is the point to marry the data").
-- int_player_daily UNION ALLs this model under its ESPN branch; every
-- downstream fact (fct_player_daily_performance, fct_player_position_pts)
-- sees one shape.
--
-- Assembly: fct_cbs_player_game_attribution (who owned each priced game +
-- the day's active state + provenance) x int_cbs__player_game_points (the
-- wide priced line), aggregated from game grain to DAY grain -- a
-- doubleheader is one row with games_played=2; a pre-universal-DH pitcher's
-- batting day merges both discipline lines onto one row (CBS credits an
-- active player's whole line, no slot-category filter -- exactly how the
-- MLB-63 reconciliation validated 2.1-4.7% against official standings).
--
-- Column semantics vs the ESPN branch:
--   scoring_period   the date as YYYYMMDD int (ESPN: platform period id).
--                    game_date carries the real DATE either way.
--   matchup_period   NULL -- CBS period boundaries don't exist historically;
--                    period-scoped surfaces read stg_cbs__standings instead.
--   player_id        TRY_TO_NUMBER(cbs id): real ids + the 900/901 pseudo
--                    pair are numeric; the ui-only-<mlbam> synthetic
--                    identities are NULL here. player_key is the grain.
--   lineup_slot      captured deployed slot for 2026 (C/1B/../P, RS);
--                    'ACT'/'RS' for reconstructed lineup-era days;
--                    'EST' where the state is estimated (2004-2020).
--   active_weight    the scoring weight: 1/0 where the state is known,
--                    the start-share estimator where estimated, NULL where
--                    membership is confirmed but activity unknown.
--   eligible_slots   date-scoped derived eligibility (int_cbs__eligibility_
--                    windows), scope-guarded through the crosswalk so the
--                    Ohtani pseudo-ids read like CBS's own cards (900 'DH',
--                    901 'P'), floored to ['DH'] for fieldless hitters --
--                    mirroring CBS's captured display.
--   per-stat columns CBS's scored categories land on their ESPN-shape
--                    homonyms (bb->b_bb, s->sv, hd->hld, ha->p_h, bbi->p_bb);
--                    IRSTR has no per-stat column (ESPN never scored it) and
--                    rides inside total_pitching_stat_pts -- the catch-all
--                    totals are the authoritative points either way.
--                    Unscored/unmapped columns are 0, never NULL, so
--                    downstream SUMs stay clean.
--
-- Grain: one row per (league_key, season_year, scoring_period, team_id,
-- player_key, lineup_slot) -- lineup_slot is constant within a player-day,
-- so (league, season, day, team, player) is unique too. A mid-day trade
-- (two franchises credited on one date) stays two rows via team_id, same
-- as ESPN's multi-slot days.

{{ config(materialized='table') }}

with attr as (
    select * from {{ ref('fct_cbs_player_game_attribution') }}
),

engine as (
    select * from {{ ref('int_cbs__player_game_points') }}
),

xwalk as (
    select cbs_player_id, mlbam_id, stat_group_scope
    from {{ ref('stg_cbs__mlbam_crosswalk') }}
),

-- Windows through the two-way scope guard: a '(Batter)' id never shows P,
-- a '(Pitcher)' id shows ONLY P -- CBS's own split-player representation.
scoped_windows as (
    select
        x.cbs_player_id,
        w.league_key,
        w.season_year,
        w.cbs_position,
        w.eligible_from,
        w.eligibility_source,
        decode(w.cbs_position, 'C', 1, '1B', 2, '2B', 3, '3B', 4,
               'SS', 5, 'OF', 6, 'DH', 7, 'P', 8, 9) as pos_rank
    from {{ ref('int_cbs__eligibility_windows') }} w
    inner join xwalk x
        on w.mlbam_id = x.mlbam_id
    where x.stat_group_scope is null
        or (x.stat_group_scope = 'hitting'  and w.cbs_position != 'P')
        or (x.stat_group_scope = 'pitching' and w.cbs_position = 'P')
),

-- Season-level primary/display position per CBS identity: the primary
-- window's position when one survives the scope guard, else the
-- lowest-ranked eligible position.
display_position as (
    select
        league_key,
        cbs_player_id,
        season_year,
        min_by(cbs_position,
               iff(eligibility_source = 'primary', 0, 100) + pos_rank) as position
    from scoped_windows
    group by 1, 2, 3
),

franchise_names as (
    select league_key, season_year, franchise_id, team_name
    from {{ ref('stg_cbs__ui_standings') }}
),

-- 2026 captured deployed slot (the attribution fact carries the A/RS state
-- but not the slot itself).
captured_slots as (
    select league_key, roster_date, player_id, max(roster_pos) as roster_pos
    from {{ ref('stg_cbs__rosters') }}
    group by 1, 2, 3
),

-- Game grain -> day grain. State/provenance are day-scoped upstream, so
-- MAX() is a passthrough, not an arbitration.
day_base as (
    select
        a.league_key,
        a.season_year,
        a.game_date,
        a.franchise_id,
        a.cbs_player_id,
        max(a.cbs_player_name)             as cbs_player_name,
        max(a.mlbam_id)                    as mlbam_id,
        max(a.team_name)                   as captured_team_name,
        max(a.provenance)                  as provenance,
        max(a.active_weight)               as active_weight,
        boolor_agg(coalesce(a.is_active, false)) as is_active_known_true,
        max(iff(a.is_active is null, 1, 0))      as is_active_unknown,
        count(distinct e.game_pk)          as games_played,

        -- CBS scored categories, day-summed
        sum(e.r)     as r,     sum(e.rbi)   as rbi,   sum(e.bb)  as bb,
        sum(e.sb)    as sb,    sum(e.tb)    as tb,
        sum(e.w)     as w,     sum(e.s)     as s,     sum(e.hd)  as hd,
        sum(e.cg)    as cg,    sum(e.qs)    as qs,    sum(e.outs) as outs,
        sum(e.k)     as k,     sum(e.ha)    as ha,    sum(e.bbi) as bbi,
        sum(e.er)    as er,    sum(e.nh)    as nh,

        sum(e.r_pts)    as r_pts,    sum(e.rbi_pts) as rbi_pts,
        sum(e.bb_pts)   as bb_pts,   sum(e.sb_pts)  as sb_pts,
        sum(e.tb_pts)   as tb_pts,
        sum(e.w_pts)    as w_pts,    sum(e.s_pts)   as s_pts,
        sum(e.hd_pts)   as hd_pts,   sum(e.cg_pts)  as cg_pts,
        sum(e.qs_pts)   as qs_pts,   sum(e.outs_pts) as outs_pts,
        sum(e.k_pts)    as k_pts,    sum(e.ha_pts)  as ha_pts,
        sum(e.bbi_pts)  as bbi_pts,  sum(e.er_pts)  as er_pts,

        sum(e.calculated_hitting_pts)  as calculated_hitting_pts,
        sum(e.calculated_pitching_pts) as calculated_pitching_pts,
        sum(e.calculated_fpts)         as calculated_fpts
    from attr a
    inner join engine e
        on a.league_key = e.league_key
        and a.cbs_player_id = e.cbs_player_id
        and a.stat_group = e.stat_group
        and a.game_date = e.game_date
        and a.game_pk = e.game_pk
        and a.game_index = e.game_index
    group by 1, 2, 3, 4, 5
),

with_slots as (
    select
        d.*,
        case
            when d.provenance = 'captured'
                then coalesce(cs.roster_pos, iff(d.active_weight = 1, 'ACT', 'RS'))
            when d.provenance = 'reconstructed_day'
                then iff(d.is_active_known_true and d.is_active_unknown = 0, 'ACT', 'RS')
            else 'EST'
        end as lineup_slot
    from day_base d
    left join captured_slots cs
        on d.league_key = cs.league_key
        and d.game_date = cs.roster_date
        and d.cbs_player_id = cs.player_id
),

eligibility as (
    -- CBS display semantics, graded against the 2026 captures: DH is
    -- universal and therefore UNLISTED unless it is the player's only
    -- position (the captures show 'DH' alone for the fieldless class and
    -- never alongside another position -- the pre-fix derived arrays
    -- disagreed on 4,011 player-days, ALL of them earned-DH-plus-others).
    -- The DH windows still exist upstream; this is presentation of the
    -- array, and the DH/U SLOTS fill universally at the renderer anyway.
    select
        league_key,
        season_year,
        game_date,
        cbs_player_id,
        iff(array_size(array_remove(raw_slots, to_variant('DH'))) > 0,
            array_remove(raw_slots, to_variant('DH')),
            raw_slots) as eligible_slots
    from (
        select
            d.league_key,
            d.season_year,
            d.game_date,
            d.cbs_player_id,
            array_agg(distinct s.cbs_position)
                within group (order by s.cbs_position) as raw_slots
        from day_base d
        inner join scoped_windows s
            on d.league_key = s.league_key
            and d.cbs_player_id = s.cbs_player_id
            and d.season_year = s.season_year
            and d.game_date >= s.eligible_from
        group by 1, 2, 3, 4
    )
)

select
    d.league_key,
    d.season_year,
    cast(null as integer)                        as matchup_period,
    to_number(to_char(d.game_date, 'YYYYMMDD'))  as scoring_period,
    d.franchise_id                               as team_id,
    coalesce(d.captured_team_name, fn.team_name) as team_name,
    cast(null as varchar)                        as team_abbrev,
    cast(null as varchar)                        as owner_name,
    try_to_number(d.cbs_player_id)::integer      as player_id,
    d.cbs_player_name                            as player_name,
    d.cbs_player_name                            as display_name,
    coalesce(dp.position,
             iff(x.stat_group_scope = 'pitching', 'P', 'DH')) as position,
    cast(null as varchar)                        as pro_team,
    coalesce(el.eligible_slots,
             iff(x.stat_group_scope = 'pitching',
                 array_construct('P'), array_construct('DH'))) as eligible_slots,
    d.lineup_slot,
    case
        when d.lineup_slot = 'RS'  then 'inactive'
        when d.lineup_slot = 'EST' then 'estimated'
        when d.lineup_slot = 'P'   then 'pitching'
        when d.lineup_slot = 'ACT'
            then iff(d.calculated_pitching_pts != 0 and d.calculated_hitting_pts = 0,
                     'pitching', 'hitting')
        else 'hitting'
    end                                          as lineup_slot_category,
    case
        when d.lineup_slot = 'EST' then null
        else d.lineup_slot != 'RS'
    end                                          as is_active_slot,
    d.games_played,
    cast(null as float)                          as platform_points,
    cast(null as float)                          as platform_hitting_pts,
    cast(null as float)                          as platform_pitching_pts,

    -- Hitting counting stats (CBS scores R/RBI/BB/SB/TB; the rest 0)
    0 as h, 0 as ab, d.bb as b_bb, 0 as b_so, 0 as hbp, 0 as sf, 0 as hr,
    d.r as r, d.rbi as rbi, d.sb as sb, 0 as cs, d.tb as tb,
    0 as singles, 0 as doubles, 0 as triples, 0 as xbh, 0 as gdp,
    0 as b_ibb, 0 as cyc,

    -- Hitting point contributions
    0 as h_pts, 0 as ab_pts, d.bb_pts as b_bb_pts, 0 as b_so_pts,
    0 as hbp_pts, 0 as sf_pts, 0 as hr_pts, d.r_pts as r_pts,
    d.rbi_pts as rbi_pts, d.sb_pts as sb_pts, 0 as cs_pts, d.tb_pts as tb_pts,
    0 as singles_pts, 0 as doubles_pts, 0 as triples_pts, 0 as xbh_pts,
    0 as gdp_pts, 0 as b_ibb_pts, 0 as cyc_pts,

    -- Pitching counting stats (W/S->SV/HD->HLD/CG/QS/OUTS/K/HA->P_H/
    -- BBI->P_BB/ER scored; IRSTR rides only in the totals; the rest 0)
    d.w as w, 0 as l, d.k as k, d.er as er, d.outs as outs, d.qs as qs,
    d.s as sv, d.hd as hld, d.ha as p_h, d.bbi as p_bb, 0 as p_hr, 0 as p_r,
    d.cg as cg, 0 as blk, 0 as wp, 0 as hbp_p, 0 as blsv, d.nh as nh,
    0 as pg, 0 as pk, 0 as sho,

    -- Pitching point contributions
    d.w_pts as w_pts, 0 as l_pts, d.k_pts as k_pts, d.er_pts as er_pts,
    d.outs_pts as outs_pts, d.qs_pts as qs_pts, d.s_pts as sv_pts,
    d.hd_pts as hld_pts, d.ha_pts as p_h_pts, d.bbi_pts as p_bb_pts,
    0 as p_hr_pts, 0 as p_r_pts, d.cg_pts as cg_pts, 0 as blk_pts,
    0 as wp_pts, 0 as hbp_p_pts, 0 as blsv_pts, 0 as nh_pts, 0 as pg_pts,
    0 as pk_pts, 0 as sho_pts,

    d.calculated_hitting_pts                     as total_hitting_stat_pts,
    d.calculated_pitching_pts                    as total_pitching_stat_pts,
    d.calculated_fpts                            as total_stat_pts,
    iff(d.calculated_fpts < 0, -d.calculated_fpts, 0) as negative_points,

    -- Shared union-layer columns (the ESPN branch fills its own)
    d.cbs_player_id                              as player_key,
    d.game_date,
    d.active_weight,
    d.provenance
from with_slots d
left join franchise_names fn
    on d.league_key = fn.league_key
    and d.season_year = fn.season_year
    and d.franchise_id = fn.franchise_id
left join display_position dp
    on d.league_key = dp.league_key
    and d.cbs_player_id = dp.cbs_player_id
    and d.season_year = dp.season_year
left join eligibility el
    on d.league_key = el.league_key
    and d.season_year = el.season_year
    and d.game_date = el.game_date
    and d.cbs_player_id = el.cbs_player_id
left join xwalk x
    on d.cbs_player_id = x.cbs_player_id
