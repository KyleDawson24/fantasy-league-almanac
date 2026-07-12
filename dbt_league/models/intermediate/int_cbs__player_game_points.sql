-- int_cbs__player_game_points.sql
-- The MLB-62 recompute: per-game calculated_ fantasy points -- universal
-- per-game stats (stg_mlb__player_game) priced by the league's own scoring
-- rules (stg_cbs__scoring_settings) and keyed to CBS player identity
-- (stg_cbs__mlbam_crosswalk). The calculated_ lens: the only per-game-capable
-- scoring view (CBS serves no per-game FPTS), the default lens of the CBS
-- almanac, and the substrate the active-only slicing (MLB-63) will filter.
--
-- Grain: one row per (league_key, cbs_player_id, stat_group, season_year,
-- game_date, game_pk, game_number, game_index) -- a player's discipline-line
-- in one MLB game, priced. game_index rides for the one known feed quirk
-- (a same-date pair of partial lines sharing a game_pk); game_number splits
-- doubleheader legs. A genuinely two-discipline game (pre-2022 pitcher
-- batting) is two rows: the hitting line and the pitching line, each scored
-- by its own group's categories.
--
-- Identity routing (MLB-68): the crosswalk join is scoped by
-- stat_group_scope -- Ohtani's hitting games land on CBS 900 '(Batter)',
-- pitching games on 901 '(Pitcher)', reported as two players; unsuffixed
-- players take both groups on their single id. mlbam ids absent from the
-- crosswalk drop at the INNER JOIN -- that includes gamelogs left behind by
-- pre-collision-fix extractions (e.g. Vlad Guerrero SENIOR): the crosswalk
-- is the population gate.
--
-- Scoring mechanics (the spike's gotchas, priced via the settings model):
--   - INN pays at OUT-granularity: the settings row is already translated
--     to canonical outs_recorded at points/3 (1 pt/out) -- this model just
--     multiplies outs.
--   - QS is DERIVED per start: games_started_pitching = 1 AND outs >= 18
--     (IP >= 6) AND earned_runs <= 3.
--   - IRSTR is DERIVED: inherited_runners - inherited_runners_scored
--     (statsapi serves no direct strand key; CBS's own direct IRSTR key is
--     the season-grain reconciliation anchor, not a per-game input).
--   - Batting categories price hitting-group rows, pitching categories
--     pitching-group rows -- automatic, because the stat map is
--     (statsapi_key, stat_group)-keyed and each group's rows carry only its
--     own discipline's canonicals.
--
-- league_key fans from the scoring settings (every league's rules price the
-- shared baseball layer independently); one league today (cbs-bsb).
--
-- Wide columns speak the league's own vocabulary (cbs_key, lowercased):
-- r/rbi/bb/sb/tb for batting; w/s/hd/cg/qs/outs/irstr/k/ha/bbi/er for
-- pitching (outs IS the INN category at pay granularity); gs/ir/irs ride
-- unpriced as derivation context. calculated_fpts = the game's total under
-- current rules -- current weights applied universally, historical rows
-- included, per the project's cross-season comparability convention.

{{ config(materialized='view') }}

with leagues as (
    select distinct league_key
    from {{ ref('stg_cbs__scoring_settings') }}
),

weights as (
    select league_key, canonical_key, points_per_unit
    from {{ ref('stg_cbs__scoring_settings') }}
),

base_long as (
    select
        mlbam_id,
        season_year,
        stat_group,
        game_date,
        game_pk,
        game_number,
        game_index,
        team_id,
        team_name,
        opponent_team_id,
        opponent_team_name,
        is_home,
        is_win,
        canonical_key,
        stat_category,
        stat_value
    from {{ ref('stg_mlb__player_game') }}
),

-- Per-game derivation inputs, pivoted from the pitching-group long rows.
pitching_games as (
    select
        mlbam_id, season_year, stat_group, game_date, game_pk, game_number,
        game_index, team_id, team_name, opponent_team_id, opponent_team_name,
        is_home, is_win,
        sum(case when canonical_key = 'games_started_pitching'    then stat_value else 0 end) as gs,
        sum(case when canonical_key = 'outs_recorded'             then stat_value else 0 end) as outs,
        sum(case when canonical_key = 'earned_runs'               then stat_value else 0 end) as er,
        sum(case when canonical_key = 'inherited_runners'         then stat_value else 0 end) as ir,
        sum(case when canonical_key = 'inherited_runners_scored'  then stat_value else 0 end) as irs
    from base_long
    where stat_group = 'pitching'
        and canonical_key in ('games_started_pitching', 'outs_recorded', 'earned_runs',
                              'inherited_runners', 'inherited_runners_scored')
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
),

-- The two categories CBS scores that no statsapi key carries, derived per
-- start/appearance and re-emitted as long rows so one weights join prices
-- everything uniformly.
derived_long as (
    select
        mlbam_id, season_year, stat_group, game_date, game_pk, game_number,
        game_index, team_id, team_name, opponent_team_id, opponent_team_name,
        is_home, is_win,
        'quality_starts'   as canonical_key,
        'pitching'         as stat_category,
        iff(gs = 1 and outs >= 18 and er <= 3, 1, 0)::float as stat_value
    from pitching_games

    union all

    select
        mlbam_id, season_year, stat_group, game_date, game_pk, game_number,
        game_index, team_id, team_name, opponent_team_id, opponent_team_name,
        is_home, is_win,
        'inherited_runners_stranded' as canonical_key,
        'pitching'                   as stat_category,
        (ir - irs)::float            as stat_value
    from pitching_games
),

scored_long as (
    select
        lg.league_key,
        l.*,
        l.stat_value * coalesce(w.points_per_unit, 0) as stat_points
    from (
        select * from base_long
        union all
        select * from derived_long
    ) l
    cross join leagues lg
    left join weights w
        on lg.league_key = w.league_key
        and l.canonical_key = w.canonical_key
),

game_wide as (
    select
        league_key,
        mlbam_id,
        season_year,
        stat_group,
        game_date,
        game_pk,
        game_number,
        game_index,
        team_id,
        team_name,
        opponent_team_id,
        opponent_team_name,
        is_home,
        is_win,

        -- Batting categories (scored +1 each)
        sum(case when canonical_key = 'runs'         then stat_value else 0 end) as r,
        sum(case when canonical_key = 'rbi'          then stat_value else 0 end) as rbi,
        sum(case when canonical_key = 'walks'        then stat_value else 0 end) as bb,
        sum(case when canonical_key = 'stolen_bases' then stat_value else 0 end) as sb,
        sum(case when canonical_key = 'total_bases'  then stat_value else 0 end) as tb,

        -- Pitching categories
        sum(case when canonical_key = 'wins'                        then stat_value else 0 end) as w,
        sum(case when canonical_key = 'saves'                       then stat_value else 0 end) as s,
        sum(case when canonical_key = 'holds'                       then stat_value else 0 end) as hd,
        sum(case when canonical_key = 'complete_games'              then stat_value else 0 end) as cg,
        sum(case when canonical_key = 'quality_starts'              then stat_value else 0 end) as qs,
        sum(case when canonical_key = 'outs_recorded'               then stat_value else 0 end) as outs,
        sum(case when canonical_key = 'inherited_runners_stranded'  then stat_value else 0 end) as irstr,
        sum(case when canonical_key = 'strikeouts_pitching'         then stat_value else 0 end) as k,
        sum(case when canonical_key = 'hits_allowed'                then stat_value else 0 end) as ha,
        sum(case when canonical_key = 'walks_issued'                then stat_value else 0 end) as bbi,
        sum(case when canonical_key = 'earned_runs'                 then stat_value else 0 end) as er,

        -- Unpriced derivation context
        sum(case when canonical_key = 'games_started_pitching'   then stat_value else 0 end) as gs,
        sum(case when canonical_key = 'inherited_runners'        then stat_value else 0 end) as ir,
        sum(case when canonical_key = 'inherited_runners_scored' then stat_value else 0 end) as irs,

        -- Per-category point contributions
        sum(case when canonical_key = 'runs'         then stat_points else 0 end) as r_pts,
        sum(case when canonical_key = 'rbi'          then stat_points else 0 end) as rbi_pts,
        sum(case when canonical_key = 'walks'        then stat_points else 0 end) as bb_pts,
        sum(case when canonical_key = 'stolen_bases' then stat_points else 0 end) as sb_pts,
        sum(case when canonical_key = 'total_bases'  then stat_points else 0 end) as tb_pts,
        sum(case when canonical_key = 'wins'                        then stat_points else 0 end) as w_pts,
        sum(case when canonical_key = 'saves'                       then stat_points else 0 end) as s_pts,
        sum(case when canonical_key = 'holds'                       then stat_points else 0 end) as hd_pts,
        sum(case when canonical_key = 'complete_games'              then stat_points else 0 end) as cg_pts,
        sum(case when canonical_key = 'quality_starts'              then stat_points else 0 end) as qs_pts,
        sum(case when canonical_key = 'outs_recorded'               then stat_points else 0 end) as outs_pts,
        sum(case when canonical_key = 'inherited_runners_stranded'  then stat_points else 0 end) as irstr_pts,
        sum(case when canonical_key = 'strikeouts_pitching'         then stat_points else 0 end) as k_pts,
        sum(case when canonical_key = 'hits_allowed'                then stat_points else 0 end) as ha_pts,
        sum(case when canonical_key = 'walks_issued'                then stat_points else 0 end) as bbi_pts,
        sum(case when canonical_key = 'earned_runs'                 then stat_points else 0 end) as er_pts,

        -- Catch-all totals over EVERY priced row (robust to future scored
        -- categories even if the per-stat columns above lag)
        sum(case when stat_category = 'hitting'  then stat_points else 0 end) as calculated_hitting_pts,
        sum(case when stat_category = 'pitching' then stat_points else 0 end) as calculated_pitching_pts,
        sum(stat_points)                                                      as calculated_fpts
    from scored_long
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14
)

select
    g.league_key,
    cw.cbs_player_id,
    cw.cbs_player_name,
    g.mlbam_id,
    g.season_year,
    g.stat_group,
    g.game_date,
    g.game_pk,
    g.game_number,
    g.game_index,
    g.team_id,
    g.team_name,
    g.opponent_team_id,
    g.opponent_team_name,
    g.is_home,
    g.is_win,
    g.r, g.rbi, g.bb, g.sb, g.tb,
    g.w, g.s, g.hd, g.cg, g.qs, g.outs, g.irstr, g.k, g.ha, g.bbi, g.er,
    g.gs, g.ir, g.irs,
    g.r_pts, g.rbi_pts, g.bb_pts, g.sb_pts, g.tb_pts,
    g.w_pts, g.s_pts, g.hd_pts, g.cg_pts, g.qs_pts, g.outs_pts, g.irstr_pts,
    g.k_pts, g.ha_pts, g.bbi_pts, g.er_pts,
    g.calculated_hitting_pts,
    g.calculated_pitching_pts,
    g.calculated_fpts
from game_wide g
inner join {{ ref('stg_cbs__mlbam_crosswalk') }} cw
    on g.mlbam_id = cw.mlbam_id
    and (cw.stat_group_scope is null or cw.stat_group_scope = g.stat_group)
