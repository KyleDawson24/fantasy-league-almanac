-- fct_cbs_player_game_attribution.sql
-- THE HISTORIC ACTIVE LENS (MLB-63): every priced player-game
-- (int_cbs__player_game_points) attributed to the franchise that
-- rostered the player that day, with the active-state weight and --
-- per Kyle's rule -- a PER-ROW provenance flag that says how we know:
--
--   captured               2026: the daily roster captures (deployed
--                          slot + A/RS as CBS served them). Confirmed.
--   reconstructed_day      2001-2003, 2021-2025: membership from the
--                          stint walk-back AND the day's active state
--                          from the lineup-log intervals. Confirmed
--                          (reconstruction-grade).
--   estimated_startshare   2004-2020: membership from the walk-back;
--                          active WEIGHT estimated as the player's global
--                          Start%/Own% ratio from ANY of that season's
--                          year-end anchors (the rates are player-season
--                          stats, not franchise stats -- Kyle 2026-07-14 --
--                          so a mid-season Team A stint borrows the ratio
--                          he finished with on Team B). ESTIMATE.
--   estimated_adjacent     2004-2020: membership from the walk-back, but the
--                          player is on NO year-end anchor that season, so
--                          the weight is BORROWED from his nearest anchored
--                          season (prev or next; equidistant ties -> the
--                          higher ratio). Covers the dropped-mid-season-star
--                          class (Trout '19, the Strasburg Shutdown).
--                          ESTIMATE (cross-season).
--   estimated_membership   2004-2020 stints with no usable estimator at all
--                          (own_pct 0/null, AND no anchor in ANY season --
--                          the true scrub who fell out of the league):
--                          membership confirmed, activity unknown -- weight
--                          NULL. Interactive surface filters; almanac explains.
--   sentinel               2001-2002 zero-event players (production but NO
--                          stint -- never transacted, no log line, no anchor
--                          era): assume-active (weight 1) on the synthetic
--                          holding-pen franchise 9999 '####', so they surface
--                          in PLAYER records. FENCED out of TEAM records + team
--                          pages; retired when the backfill reassigns them.
--
-- is_active is boolean where the day's state is KNOWN, NULL where
-- estimated; active_weight is the scoring weight either way (1/0 or
-- the estimator). Rows where nobody rostered the player (FA games)
-- don't exist here by construction. Ambiguity and inferred ends ride
-- as flags (is_ambiguous_name, membership_end_inferred), never as
-- silent guesses.
--
-- Grain: one row per (league_key, cbs_player_id, stat_group, game_pk,
-- game_date, game_index) -- the engine's grain, franchise-attributed.

{{ config(materialized='table') }}

with games as (
    select * from {{ ref('int_cbs__player_game_points') }}
),

-- ---------------------------------------------------------------- 2026
captured as (
    select
        g.league_key,
        g.cbs_player_id,
        g.cbs_player_name,
        g.mlbam_id,
        g.season_year,
        g.stat_group,
        g.game_date,
        g.game_pk,
        g.game_index,
        r.team_id::integer               as franchise_id,
        r.team_name,
        'captured'                       as provenance,
        'captured'                       as state_source,
        (r.roster_status = 'A')          as is_active,
        iff(r.roster_status = 'A', 1.0, 0.0) as active_weight,
        false                            as is_ambiguous_name,
        false                            as membership_end_inferred,
        false                            as attribution_contested,
        g.calculated_fpts,
        g.calculated_hitting_pts,
        g.calculated_pitching_pts
    from games g
    inner join {{ ref('stg_cbs__rosters') }} r
        on g.league_key = r.league_key
        and g.cbs_player_id = r.player_id
        and g.game_date = r.roster_date
),

-- ------------------------------------------------- 2001-2025 walk-back
-- ident = the MLB-81 join spine: mlbam where the season's name form resolved,
-- else the name key (ambiguous/uncandidated -> today's name-join fallback).
-- scope_key splits a two-way player's two disciplines.
stints as (
    select
        se.*,
        coalesce(to_varchar(se.mlbam_id), 'name:' || se.name_key) as ident,
        coalesce(se.stat_group_scope, '')                         as scope_key
    from {{ ref('int_cbs__roster_stints_effective') }} se
),

-- Year-end anchors, MLB-81 identity-resolved then RE-AGGREGATED to mlbam
-- grain (ident, scope_key). Grouping to the identity -- not decorating
-- name-keyed rows -- is what stops the LEFT JOINs below from fanning out
-- silently when two era name-forms of one player each carry an anchor row
-- (the QUALIFY would hide the duplicate while corrupting active_weight /
-- attribution_contested). Two-way splits (Ohtani 2025: batter + pitcher
-- anchor rows) stay separate via scope_key.
anchors_resolved as (
    -- MLB-120: ctx supplies the per-franchise resolution where the season-
    -- grain dim stays ambiguous -- an anchor row is franchise paperwork, so
    -- the franchise-context id applies to it by construction.
    select
        r.league_key,
        r.season_year,
        r.franchise_id,
        {{ cbs_name_key('r.player_name_raw') }}                    as name_key,
        r.est_start_share,
        r.primary_pos,
        r.roster_status,
        coalesce(ctx.mlbam_id, pid.mlbam_id)                      as mlbam_id,
        coalesce(to_varchar(coalesce(ctx.mlbam_id, pid.mlbam_id)),
                 'name:' || {{ cbs_name_key('r.player_name_raw') }}) as ident,
        coalesce(coalesce(ctx.stat_group_scope, pid.stat_group_scope), '') as scope_key
    from {{ ref('stg_cbs__ui_rosters') }} r
    left join {{ ref('dim_player_identity') }} pid
        on pid.platform = 'cbs'
        and pid.name_key = {{ cbs_name_key('r.player_name_raw') }}
        and pid.season_year = r.season_year
    left join {{ ref('int_player_identity_context') }} ctx
        on ctx.platform = 'cbs'
        and ctx.league_key = r.league_key
        and ctx.season_year = r.season_year
        and ctx.franchise_id = r.franchise_id
        and ctx.name_key = {{ cbs_name_key('r.player_name_raw') }}
),

anchors_est as (
    select
        league_key, season_year, franchise_id, ident, scope_key,
        max(mlbam_id)        as mlbam_id,
        max(name_key)        as name_key,
        max(est_start_share) as est_start_share,
        max(primary_pos)     as primary_pos,
        max(roster_status)   as anchor_status
    from anchors_resolved
    group by 1, 2, 3, 4, 5
),

-- The estimator at SEASON grain (Kyle, 2026-07-14): Own%/Start% are global
-- CBS stats about the PLAYER, not the franchise, so the ratio from any
-- year-end anchor row covers every stint that season -- a mid-season stint
-- on Team A borrows the ratio the player finished with on Team B.
-- (anchor_STATUS stays franchise-scoped in anchors_est above.)
anchors_est_season as (
    select
        league_key, season_year, ident, scope_key,
        max(est_start_share) as est_start_share
    from anchors_resolved
    group by 1, 2, 3, 4
),

-- Adjacent-anchor borrow (Kyle, 2026-07-14): a 2004-2020 stint whose player
-- sits on NO year-end anchor THAT season (dropped and gone by year's end --
-- Trout '19, the Strasburg Shutdown, Sale/deGrom) borrows the ratio from his
-- NEAREST anchored season, previous or next; an equidistant tie breaks to the
-- HIGHER ratio. No anchored season anywhere -> left dark (weight NULL).
-- Keyed on the mlbam ident so a player's seasons connect across name-form drift.
anchors_adjacent as (
    select league_key, season_year, ident, scope_key, borrowed_ratio
    from (
        select
            st.league_key,
            st.season_year,
            st.ident,
            st.scope_key,
            ar.est_start_share as borrowed_ratio,
            row_number() over (
                partition by st.league_key, st.ident, st.scope_key, st.season_year
                -- MLB-134 -- abs() manufactures ties (an anchor two
                -- seasons back and two seasons forward are always
                -- equidistant). est_start_share breaks most, and
                -- because it is ALSO the only column taken from `ar`,
                -- a remaining tie emits identical rows -- the value is
                -- already deterministic. season_year pins the row
                -- choice too, preferring the later anchor.
                order by abs(ar.season_year - st.season_year) asc,
                         ar.est_start_share desc nulls last,
                         ar.season_year desc nulls last
            ) as rn
        from (
            select distinct league_key, season_year, ident, scope_key
            from stints where season_year between 2004 and 2020
        ) st
        join anchors_est_season ar
            on ar.league_key = st.league_key
            and ar.ident = st.ident
            and ar.scope_key = st.scope_key
            and ar.season_year != st.season_year
            and ar.est_start_share is not null
    )
    where rn = 1
),

reconstructed as (
    select
        g.league_key,
        g.cbs_player_id,
        g.cbs_player_name,
        g.mlbam_id,
        g.season_year,
        g.stat_group,
        g.game_date,
        g.game_pk,
        g.game_index,
        s.franchise_id,
        cast(null as varchar)            as team_name,
        case
            when li.state is not null        then 'reconstructed_day'
            when s.season_year between 2004 and 2020
                 and aes.est_start_share is not null
                                             then 'estimated_startshare'
            when s.season_year between 2004 and 2020
                 and adj.borrowed_ratio is not null
                                             then 'estimated_adjacent'
            when s.season_year between 2004 and 2020
                                             then 'estimated_membership'
            when ae.anchor_status is not null then 'reconstructed_day'
            else 'estimated_membership'   -- lineup era, no events, no anchor
        end                              as provenance,
        case
            when li.state is not null        then li.state_source
            when s.season_year between 2004 and 2020
                 and aes.est_start_share is not null
                                             then 'startshare'
            when s.season_year between 2004 and 2020
                 and adj.borrowed_ratio is not null
                                             then 'adjacent'
            when s.season_year not between 2004 and 2020
                 and ae.anchor_status is not null
                                             then 'anchor_hold'
        end                              as state_source,
        case
            when li.state is not null        then li.state = 'A'
            when s.season_year between 2004 and 2020
                                             then null
            when ae.anchor_status is not null then ae.anchor_status = 'A'
        end                              as is_active,
        case
            when li.state is not null        then iff(li.state = 'A', 1.0, 0.0)
            when s.season_year between 2004 and 2020
                 then coalesce(aes.est_start_share, adj.borrowed_ratio)
            when ae.anchor_status is not null then iff(ae.anchor_status = 'A', 1.0, 0.0)
        end                              as active_weight,
        coalesce(s.is_ambiguous_name, false)               as is_ambiguous_name,
        (s.was_truncated or coalesce(s.missing_departure, false))
                                                           as membership_end_inferred,
        -- Contested = more than one FRANCHISE claims the game (MLB-118/120,
        -- Kyle: two same-franchise candidate stints -- era name-forms of one
        -- player, Tony A./F. Pena -- credit the same team either way, so the
        -- flag lied when it counted stints. Which STINT wins within a
        -- franchise stays the qualify's stable pick; which FRANCHISE gets the
        -- game is the only genuine uncertainty).
        -- (min != max stands in for COUNT(DISTINCT) > 1, which Snowflake
        -- does not allow over a window.)
        (min(s.franchise_id) over (
            partition by g.league_key, g.cbs_player_id, g.stat_group,
                         g.game_pk, g.game_date, g.game_index)
         <>
         max(s.franchise_id) over (
            partition by g.league_key, g.cbs_player_id, g.stat_group,
                         g.game_pk, g.game_date, g.game_index))
                                                           as attribution_contested,
        g.calculated_fpts,
        g.calculated_hitting_pts,
        g.calculated_pitching_pts
    from games g
    -- S1 games <-> stints: id-first on the mlbam spine (fixes K-Rod's Angels
    -- peak, the middle-initial class, Ohtani 2018-24, Stanton 2010-11), with
    -- the ORIGINAL name-join kept as a fallback for stints whose name is
    -- ambiguous that season (mlbam NULL) -- so this is a strict superset of
    -- the prior behaviour, never a regression. The scope guard splits a
    -- two-way player's disciplines (a unified entry carries scope NULL and
    -- matches either).
    inner join stints s
        on g.league_key = s.league_key
        and g.season_year = s.season_year
        and (
            (s.mlbam_id is not null
                and g.mlbam_id = s.mlbam_id
                and (s.stat_group_scope is null or s.stat_group_scope = g.stat_group))
            or
            (s.mlbam_id is null
                and {{ cbs_name_key('g.cbs_player_name') }} = s.name_key)
        )
        and g.game_date >= s.stint_start
        and g.game_date < s.attribution_end_exclusive
    -- S2/S3/S4 stint <-> lineup/anchors: both sides now carry the mlbam ident,
    -- so join on it (+ scope). Symmetric ident = mlbam where resolved, name
    -- key where not -- preserving today's name match for the ambiguous class.
    left join {{ ref('int_cbs__lineup_intervals') }} li
        on s.league_key = li.league_key
        and s.season_year = li.season_year
        and s.franchise_id = li.franchise_id
        and s.ident = coalesce(to_varchar(li.mlbam_id), 'name:' || li.name_key)
        and s.scope_key = coalesce(li.stat_group_scope, '')
        and g.game_date >= li.state_start
        and g.game_date < li.state_end_exclusive
    left join anchors_est ae
        on s.league_key = ae.league_key
        and s.season_year = ae.season_year
        and s.franchise_id = ae.franchise_id
        and s.ident = ae.ident
        and s.scope_key = ae.scope_key
    left join anchors_est_season aes
        on s.league_key = aes.league_key
        and s.season_year = aes.season_year
        and s.ident = aes.ident
        and s.scope_key = aes.scope_key
    left join anchors_adjacent adj
        on s.league_key = adj.league_key
        and s.season_year = adj.season_year
        and s.ident = adj.ident
        and s.scope_key = adj.scope_key
    where g.season_year between 2001 and 2025
    -- One attribution per game row, ALWAYS: an ambiguous name (two real
    -- players sharing it) can match two teams' stints -- prefer the
    -- stint whose anchored position matches the game's discipline
    -- (catcher games don't credit the reliever of the same name), then
    -- the most recent stint. Losers surface via attribution_contested.
    -- The trailing keys are a DETERMINISM guard (MLB-117). Position-match and
    -- stint_start alone are not unique: one player can hold two stints with the
    -- same start -- the same franchise adding him twice on one day under two
    -- name forms, or a same-day add/drop beside a real rostering. Until MLB-117
    -- those siblings truncated each other to zero, so only one ever reached
    -- here and this partition never saw a tie (attribution_contested read 0
    -- league-wide). Now that both survive, the tie is real, and without a
    -- unique final key row_number() would pick between them differently
    -- between builds -- moving the same games this ticket just stopped moving.
    -- name_key then stint_index is arbitrary but STABLE, which is the whole
    -- requirement; genuinely contested rows still surface via
    -- attribution_contested.
    qualify row_number() over (
        partition by g.league_key, g.cbs_player_id, g.stat_group,
                     g.game_pk, g.game_date, g.game_index
        order by iff(
            case when ae.primary_pos in ('SP', 'RP', 'P')
                 then g.stat_group = 'pitching'
                 else g.stat_group = 'hitting' end, 0, 1),
            s.stint_start desc,
            s.name_key,
            s.stint_index
    ) = 1
),

-- SENTINEL (Kyle, 2026-07-14): the 2001-2002 zero-event players -- real
-- gamelog production but NO stint at all (never transacted, no log line, no
-- year-end anchor to bootstrap membership). Parked on the synthetic holding-pen
-- franchise at weight 1 -- assume-active until the backfill learns otherwise --
-- so their production surfaces in PLAYER/league records now. The record book
-- FENCES the holding pen out of every TEAM aggregation (a holding pen isn't a
-- franchise) and it gets no team page (not in the active set); the 2001-2002
-- real teams' team records stay incomplete until the backfill reassigns these
-- players off it. Law 1 already applied upstream, so a sentinel pitcher
-- carries pitching rows only.
--
-- The id and label come from the holding_pen_* project vars (MLB-115) rather
-- than literals, so the placeholder can be swapped in one place.
sentinel as (
    select
        g.league_key,
        g.cbs_player_id,
        g.cbs_player_name,
        g.mlbam_id,
        g.season_year,
        g.stat_group,
        g.game_date,
        g.game_pk,
        g.game_index,
        {{ var('holding_pen_franchise_id') }} as franchise_id,
        cast('{{ var("holding_pen_label") }}' as varchar) as team_name,
        'sentinel'                       as provenance,
        'sentinel'                       as state_source,
        true                             as is_active,
        1.0::double                       as active_weight,
        false                            as is_ambiguous_name,
        false                            as membership_end_inferred,
        false                            as attribution_contested,
        g.calculated_fpts,
        g.calculated_hitting_pts,
        g.calculated_pitching_pts
    from games g
    where g.season_year in (2001, 2002)
      -- Complement of the reconstructed attribution BY CONSTRUCTION (X1): a
      -- 2001-2002 game gets a sentinel row iff the walk-back did NOT already
      -- attribute it. Anti-joining reconstructed on the game grain (rather than
      -- re-deriving S1's identity predicate) keeps sentinel and reconstructed
      -- exactly mutually exclusive with no hand-synced second copy, and avoids
      -- the multi-correlated NOT EXISTS Snowflake can't decorrelate.
      and not exists (
          select 1 from reconstructed rc
          where rc.league_key = g.league_key
            and rc.cbs_player_id = g.cbs_player_id
            and rc.stat_group = g.stat_group
            and rc.game_pk = g.game_pk
            and rc.game_date = g.game_date
            and rc.game_index = g.game_index
      )
)

, unioned as (

select * from captured
union all
select * from reconstructed
union all
select * from sentinel

)

-- Display resolves through the franchise dim (MLB-113). This fact is read
-- directly by the CBS renderer and does not pass through the shared seam at
-- fct_player_daily_performance, so it makes the join itself -- once over the
-- union, which keeps all three branches on one rule.
--
-- NULLs are preserved deliberately. The reconstructed branch carries no team
-- name by design (it infers a franchise from stints, it does not capture a
-- name), and this ticket is about where a DISPLAYED name comes from, not about
-- inventing names for rows that never had one. Filling them would be a
-- semantic change wearing a display change's clothes.
--
-- The sentinel branch resolves to nothing -- the holding pen has no observed
-- seasons by construction -- so it keeps the label it was built with, which is
-- already the holding_pen_label var.
select u.* replace (
    iff(u.team_name is null,
        null,
        coalesce(d.canonical_name, u.team_name)) as team_name
)
from unioned u
left join {{ ref('dim_franchise_season') }} d
    on u.league_key = d.league_key
    and cast(u.franchise_id as varchar) = d.franchise_id
    and u.season_year = d.season_year
