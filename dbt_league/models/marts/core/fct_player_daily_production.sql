-- fct_player_daily_production.sql
-- The FULL real-life stat line for every league x game day x rosterable asset
-- (master-table contract v0.3, warehouse asks W1 + W6; MLB-319).
--
-- WHY IT EXISTS. fct_player_daily_performance carries the components a
-- league CREDITED: ESPN rows drop the discipline a started player's slot did
-- not count (the MLB-174 off-slot case), and CBS rows carry only the
-- categories CBS scores (unscored columns are 0). Neither carries plate
-- appearances, batters faced, sacrifice bunts or per-discipline games. The
-- dashboard's "Total MLB production" lens, its +/- baseline and its MLB stat
-- columns need the whole line. This model is that line, in one vocabulary,
-- for every league; the atom joins it and the credited shares still decide
-- what counted.
--
-- TWO SOURCES, ONE SHAPE, NO PLATFORM CONDITION. Each branch reads a
-- relation that only ever holds its own platform's rows:
--   * stg_player_stat_breakdowns -- the platform's own per-day breakdown,
--     where a platform serves one (ESPN today). Keys mapped exactly as the
--     published dashboard already maps them (stat_export.MAPPING), so the
--     numbers it shows do not move.
--   * stg_mlb__player_game via fct_cbs_player_game_attribution -- the
--     universal MLB game-log spine, for platforms whose players resolve to
--     MLBAM ids (CBS today). The attribution already says which spine games
--     each rosterable asset owns, including the stat group, so a two-way
--     player's split assets (900 batter / 901 pitcher) each take only their
--     own discipline.
-- When MLB-129 resolves ESPN players to MLBAM, the first branch can retire in
-- favour of the second without any consumer changing.
--
-- DERIVED EVENTS on the spine branch are computed per GAME, then summed:
-- cycle (1B, 2B, 3B and HR in one game), quality start (started, 18+ outs,
-- 3 or fewer earned runs), no-hitter (complete game, no hits allowed). Perfect
-- game stays NULL there: the spine carries no fielding errors, so it cannot
-- be told from a no-walk no-hitter. NULL is "not derivable", never zero.
--
-- PA follows the definition the dashboard already publishes, ab + bb + hbp +
-- sf. The contract's bref definition adds sh; sh is carried separately so
-- either can be derived.
--
-- GRAIN: one row per (league_key, game_date, player_key).

{{ config(materialized='table') }}

with day_keys as (
    -- The league's own mapping from its day key to a calendar date, taken
    -- from rows that carry both (validated equal to opener + ordinal - 1).
    select distinct league_key, season_year, scoring_period, game_date
    from {{ ref('fct_player_daily_performance') }}
),

breakdown_line as (
    select
        b.league_key,
        b.season_year,
        d.game_date,
        cast(b.player_id as {{ dbt.type_string() }}) as player_key,
        sum(case when b.stat_name = 'G' then b.stat_value end)      as batting_games,
        sum(case when b.stat_name = 'AB' then b.stat_value end)     as ab,
        sum(case when b.stat_name = 'H' then b.stat_value end)      as h,
        sum(case when b.stat_name = '1B' then b.stat_value end)     as singles,
        sum(case when b.stat_name = '2B' then b.stat_value end)     as doubles,
        sum(case when b.stat_name = '3B' then b.stat_value end)     as triples,
        sum(case when b.stat_name = 'HR' then b.stat_value end)     as hr,
        sum(case when b.stat_name = 'XBH' then b.stat_value end)    as xbh,
        sum(case when b.stat_name = 'TB' then b.stat_value end)     as tb,
        sum(case when b.stat_name = 'R' then b.stat_value end)      as r,
        sum(case when b.stat_name = 'RBI' then b.stat_value end)    as rbi,
        sum(case when b.stat_name = 'B_BB' then b.stat_value end)   as bb,
        sum(case when b.stat_name = 'B_IBB' then b.stat_value end)  as ibb,
        sum(case when b.stat_name = 'B_SO' then b.stat_value end)   as so,
        sum(case when b.stat_name = 'HBP' then b.stat_value end)    as hbp,
        sum(case when b.stat_name = 'SF' then b.stat_value end)     as sf,
        sum(case when b.stat_name = 'SH' then b.stat_value end)     as sh,
        sum(case when b.stat_name = 'SB' then b.stat_value end)     as sb,
        sum(case when b.stat_name = 'CS' then b.stat_value end)     as cs,
        sum(case when b.stat_name = 'GDP' then b.stat_value end)    as gdp,
        sum(case when b.stat_name = '30' then b.stat_value end)     as cyc,
        sum(case when b.stat_name = 'GP' then b.stat_value end)     as pitching_games,
        sum(case when b.stat_name = 'GS' then b.stat_value end)     as pitching_starts,
        sum(case when b.stat_name = 'OUTS' then b.stat_value end)   as outs,
        sum(case when b.stat_name = 'W' then b.stat_value end)      as w,
        sum(case when b.stat_name = 'L' then b.stat_value end)      as l,
        sum(case when b.stat_name = 'K' then b.stat_value end)      as k,
        sum(case when b.stat_name = 'ER' then b.stat_value end)     as er,
        sum(case when b.stat_name = 'QS' then b.stat_value end)     as qs,
        sum(case when b.stat_name = 'SV' then b.stat_value end)     as sv,
        sum(case when b.stat_name = 'HLD' then b.stat_value end)    as hld,
        sum(case when b.stat_name = 'BLSV' then b.stat_value end)   as blsv,
        sum(case when b.stat_name = 'P_H' then b.stat_value end)    as p_h,
        sum(case when b.stat_name = 'P_BB' then b.stat_value end)   as p_bb,
        sum(case when b.stat_name = 'P_IBB' then b.stat_value end)  as p_ibb,
        sum(case when b.stat_name = 'P_HR' then b.stat_value end)   as p_hr,
        sum(case when b.stat_name = 'P_R' then b.stat_value end)    as p_r,
        sum(case when b.stat_name = 'CG' then b.stat_value end)     as cg,
        sum(case when b.stat_name = '64' then b.stat_value end)     as sho,
        sum(case when b.stat_name = 'NH' then b.stat_value end)     as nh,
        sum(case when b.stat_name = 'PG' then b.stat_value end)     as pg,
        sum(case when b.stat_name = 'BLK' then b.stat_value end)    as blk,
        sum(case when b.stat_name = 'WP' then b.stat_value end)     as wp,
        sum(case when b.stat_name = 'HBP_P' then b.stat_value end)  as hbp_p,
        sum(case when b.stat_name = 'PK' then b.stat_value end)     as pk,
        sum(case when b.stat_name = 'TBF' then b.stat_value end)    as bf,
        cast(null as double)                                        as gf,
        'platform_breakdown' as production_source
    from {{ ref('stg_player_stat_breakdowns') }} b
    inner join day_keys d
        on d.league_key = b.league_key
        and d.season_year = b.season_year
        and d.scoring_period = b.scoring_period
    group by 1, 2, 3, 4
),

spine_game as (
    -- One row per (mlbam, stat group, game) in the spine's canonical keys.
    select
        mlbam_id, stat_group, game_pk, game_date,
        sum(case when canonical_key = 'games_played' then stat_value end)            as games,
        sum(case when canonical_key = 'at_bats' then stat_value end)                 as ab,
        sum(case when canonical_key = 'hits' then stat_value end)                    as h,
        sum(case when canonical_key = 'doubles' then stat_value end)                 as doubles,
        sum(case when canonical_key = 'triples' then stat_value end)                 as triples,
        sum(case when canonical_key = 'home_runs' then stat_value end)               as hr,
        sum(case when canonical_key = 'total_bases' then stat_value end)             as tb,
        sum(case when canonical_key = 'runs' then stat_value end)                    as r,
        sum(case when canonical_key = 'rbi' then stat_value end)                     as rbi,
        sum(case when canonical_key = 'walks' then stat_value end)                   as bb,
        sum(case when canonical_key = 'intentional_walks' then stat_value end)       as ibb,
        sum(case when canonical_key = 'strikeouts_batting' then stat_value end)      as so,
        sum(case when canonical_key = 'hit_by_pitch' then stat_value end)            as hbp,
        sum(case when canonical_key = 'sac_flies' then stat_value end)               as sf,
        sum(case when canonical_key = 'sac_bunts' then stat_value end)               as sh,
        sum(case when canonical_key = 'stolen_bases' then stat_value end)            as sb,
        sum(case when canonical_key = 'caught_stealing' then stat_value end)         as cs,
        sum(case when canonical_key = 'gidp' then stat_value end)                    as gdp,
        sum(case when canonical_key = 'appearances_pitching' then stat_value end)    as pitching_games,
        sum(case when canonical_key = 'games_started_pitching' then stat_value end)  as pitching_starts,
        sum(case when canonical_key = 'outs_recorded' then stat_value end)           as outs,
        sum(case when canonical_key = 'wins' then stat_value end)                    as w,
        sum(case when canonical_key = 'losses' then stat_value end)                  as l,
        sum(case when canonical_key = 'strikeouts_pitching' then stat_value end)     as k,
        sum(case when canonical_key = 'earned_runs' then stat_value end)             as er,
        sum(case when canonical_key = 'saves' then stat_value end)                   as sv,
        sum(case when canonical_key = 'holds' then stat_value end)                   as hld,
        sum(case when canonical_key = 'blown_saves' then stat_value end)             as blsv,
        sum(case when canonical_key = 'hits_allowed' then stat_value end)            as p_h,
        sum(case when canonical_key = 'walks_issued' then stat_value end)            as p_bb,
        sum(case when canonical_key = 'intentional_walks_issued' then stat_value end) as p_ibb,
        sum(case when canonical_key = 'home_runs_allowed' then stat_value end)       as p_hr,
        sum(case when canonical_key = 'runs_allowed' then stat_value end)            as p_r,
        sum(case when canonical_key = 'complete_games' then stat_value end)          as cg,
        sum(case when canonical_key = 'shutouts' then stat_value end)                as sho,
        sum(case when canonical_key = 'balks' then stat_value end)                   as blk,
        sum(case when canonical_key = 'wild_pitches' then stat_value end)            as wp,
        sum(case when canonical_key = 'hit_batsmen' then stat_value end)             as hbp_p,
        sum(case when canonical_key = 'pickoffs' then stat_value end)                as pk,
        sum(case when canonical_key = 'batters_faced' then stat_value end)           as bf,
        sum(case when canonical_key = 'games_finished' then stat_value end)          as gf
    from {{ ref('stg_mlb__player_game') }}
    group by 1, 2, 3, 4
),

attributed_game as (
    -- The asset's own games only: attribution names the stat group each
    -- asset owns, so a split two-way player never double-counts.
    select distinct
        a.league_key, a.season_year, a.game_date, a.cbs_player_id as player_key,
        a.stat_group, a.game_pk, a.mlbam_id
    from {{ ref('fct_cbs_player_game_attribution') }} a
),

spine_line as (
    select
        a.league_key,
        a.season_year,
        a.game_date,
        a.player_key,
        sum(case when a.stat_group = 'hitting' then s.games end)           as batting_games,
        sum(case when a.stat_group = 'hitting' then s.ab end)              as ab,
        sum(case when a.stat_group = 'hitting' then s.h end)               as h,
        sum(case when a.stat_group = 'hitting'
                 then s.h - coalesce(s.doubles, 0) - coalesce(s.triples, 0) - coalesce(s.hr, 0) end) as singles,
        sum(case when a.stat_group = 'hitting' then s.doubles end)         as doubles,
        sum(case when a.stat_group = 'hitting' then s.triples end)         as triples,
        sum(case when a.stat_group = 'hitting' then s.hr end)              as hr,
        sum(case when a.stat_group = 'hitting'
                 then coalesce(s.doubles, 0) + coalesce(s.triples, 0) + coalesce(s.hr, 0) end) as xbh,
        sum(case when a.stat_group = 'hitting' then s.tb end)              as tb,
        sum(case when a.stat_group = 'hitting' then s.r end)               as r,
        sum(case when a.stat_group = 'hitting' then s.rbi end)             as rbi,
        sum(case when a.stat_group = 'hitting' then s.bb end)              as bb,
        sum(case when a.stat_group = 'hitting' then s.ibb end)             as ibb,
        sum(case when a.stat_group = 'hitting' then s.so end)              as so,
        sum(case when a.stat_group = 'hitting' then s.hbp end)             as hbp,
        sum(case when a.stat_group = 'hitting' then s.sf end)              as sf,
        sum(case when a.stat_group = 'hitting' then s.sh end)              as sh,
        sum(case when a.stat_group = 'hitting' then s.sb end)              as sb,
        sum(case when a.stat_group = 'hitting' then s.cs end)              as cs,
        sum(case when a.stat_group = 'hitting' then s.gdp end)             as gdp,
        sum(case when a.stat_group = 'hitting'
                  and s.h - coalesce(s.doubles, 0) - coalesce(s.triples, 0) - coalesce(s.hr, 0) >= 1
                  and s.doubles >= 1 and s.triples >= 1 and s.hr >= 1 then 1
                 when a.stat_group = 'hitting' then 0 end)                 as cyc,
        sum(case when a.stat_group = 'pitching' then s.pitching_games end) as pitching_games,
        sum(case when a.stat_group = 'pitching' then s.pitching_starts end) as pitching_starts,
        sum(case when a.stat_group = 'pitching' then s.outs end)           as outs,
        sum(case when a.stat_group = 'pitching' then s.w end)              as w,
        sum(case when a.stat_group = 'pitching' then s.l end)              as l,
        sum(case when a.stat_group = 'pitching' then s.k end)              as k,
        sum(case when a.stat_group = 'pitching' then s.er end)             as er,
        sum(case when a.stat_group = 'pitching' and s.pitching_starts >= 1
                  and s.outs >= 18 and coalesce(s.er, 0) <= 3 then 1
                 when a.stat_group = 'pitching' then 0 end)                as qs,
        sum(case when a.stat_group = 'pitching' then s.sv end)             as sv,
        sum(case when a.stat_group = 'pitching' then s.hld end)            as hld,
        sum(case when a.stat_group = 'pitching' then s.blsv end)           as blsv,
        sum(case when a.stat_group = 'pitching' then s.p_h end)            as p_h,
        sum(case when a.stat_group = 'pitching' then s.p_bb end)           as p_bb,
        sum(case when a.stat_group = 'pitching' then s.p_ibb end)          as p_ibb,
        sum(case when a.stat_group = 'pitching' then s.p_hr end)           as p_hr,
        sum(case when a.stat_group = 'pitching' then s.p_r end)            as p_r,
        sum(case when a.stat_group = 'pitching' then s.cg end)             as cg,
        sum(case when a.stat_group = 'pitching' then s.sho end)            as sho,
        sum(case when a.stat_group = 'pitching' and s.cg >= 1 and s.p_h = 0 then 1
                 when a.stat_group = 'pitching' then 0 end)                as nh,
        cast(null as double)                                               as pg,
        sum(case when a.stat_group = 'pitching' then s.blk end)            as blk,
        sum(case when a.stat_group = 'pitching' then s.wp end)             as wp,
        sum(case when a.stat_group = 'pitching' then s.hbp_p end)          as hbp_p,
        sum(case when a.stat_group = 'pitching' then s.pk end)             as pk,
        sum(case when a.stat_group = 'pitching' then s.bf end)             as bf,
        sum(case when a.stat_group = 'pitching' then s.gf end)             as gf,
        'mlb_spine' as production_source
    from attributed_game a
    inner join spine_game s
        on s.mlbam_id = a.mlbam_id
        and s.stat_group = a.stat_group
        and s.game_pk = a.game_pk
    group by 1, 2, 3, 4
),

unioned as (
    select * from breakdown_line
    union all
    select * from spine_line
)

select
    *,
    -- The dashboard's published PA definition; sh rides beside it.
    coalesce(ab, 0) + coalesce(bb, 0) + coalesce(hbp, 0) + coalesce(sf, 0) as pa
from unioned
