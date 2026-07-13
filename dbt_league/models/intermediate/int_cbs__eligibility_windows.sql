-- int_cbs__eligibility_windows.sql
-- CBS position eligibility as DATE-SCOPED WINDOWS, implementing the league's
-- own captured rule (rules page, verbatim):
--
--   "Players are eligible at their primary position, plus positions they've
--    played 20 games last year or 10 games this year."
--   "Everyone is eligible at DH."
--
-- with ESPN's after-achievement semantics (Kyle, 2026-07-13): points count
-- toward a position only AFTER eligibility is achieved. Concretely, one row
-- per (league_key, mlbam_id, season_year, cbs_position) with the DATE the
-- window opens:
--
--   primary        primary position -> eligible from opening day (Jan 1 of
--                  the season; any game date qualifies). Primary is DERIVED
--                  as the argmax-games position of the PRIOR season, falling
--                  back to the current season for players with no prior-year
--                  appearances (rookies, returners) -- CBS's own label isn't
--                  served historically, so this is the estimator. Graded
--                  against the 2026 captured eligible_positions (see the
--                  eligibility grading note in WALKBACK_PROGRESS.md).
--   prior_year_20  >= 20 games at the position LAST season -> eligible from
--                  opening day. Pre-league seasons count (the fielding sweep
--                  carries full careers, so 2001 eligibility reads 2000).
--   in_season_10   the 10th game at the position THIS season -> eligible
--                  from that game's DATE, inclusive.
--
-- DH-for-all deliberately does NOT ride here: it is slot semantics, not an
-- earned window. Consumers (int_cbs__player_daily) floor empty arrays to
-- ['DH'] -- mirroring CBS's own display for fieldless hitters -- and the
-- renderer treats the DH and U SLOTS as universal-fill. Pitchers similarly
-- never earn DH here, matching the captures (Ohtani-Pitcher shows 'P', not
-- 'P,DH'; the DH-ness lives on the Batter pseudo-id via the crosswalk's
-- stat_group_scope guard at consumption).
--
-- Games-by-position merges BOTH sources per (player, season, position):
-- season-grain fielding (authoritative totals, pre-league years included)
-- and per-game positions (the achievement dates; also the fallback where a
-- fielding fetch failed). GREATEST of the two counts -- they agree in the
-- overwhelming case, and the merged count is robust to either side's gaps.
--
-- league_key fans from the scoring settings like the points engine: the
-- rule is this league's, and the windows land league-keyed so the shape
-- matches everything else the fantasy layer serves. One league today.
--
-- Grain: one row per (league_key, mlbam_id, season_year, cbs_position) --
-- the EARLIEST window wins when several sources grant the same position
-- (primary beats prior_year_20 beats in_season_10 on same-date ties, so
-- eligibility_source reads as the strongest claim).

{{ config(materialized='table') }}

with leagues as (
    select distinct league_key from {{ ref('stg_cbs__scoring_settings') }}
),

fielding_games as (
    select
        mlbam_id,
        season_year,
        cbs_position,
        sum(games)          as games,
        sum(games_started)  as games_started
    from {{ ref('stg_mlb__fielding_seasons') }}
    where cbs_position is not null
    group by 1, 2, 3
),

gamepos_games as (
    select
        mlbam_id,
        season_year,
        cbs_position,
        count(distinct game_pk) as games
    from {{ ref('stg_mlb__game_positions') }}
    where cbs_position is not null
    group by 1, 2, 3
),

pos_season_games as (
    select
        coalesce(f.mlbam_id, g.mlbam_id)         as mlbam_id,
        coalesce(f.season_year, g.season_year)   as season_year,
        coalesce(f.cbs_position, g.cbs_position) as cbs_position,
        greatest(coalesce(f.games, 0), coalesce(g.games, 0)) as games,
        coalesce(f.games_started, 0)             as games_started
    from fielding_games f
    full outer join gamepos_games g
        on f.mlbam_id = g.mlbam_id
        and f.season_year = g.season_year
        and f.cbs_position = g.cbs_position
),

-- Seasons a player actually appears in (either source) -- the primary
-- fallback below must never mint windows for seasons a player sat out.
player_seasons as (
    select distinct mlbam_id, season_year from pos_season_games
),

-- Primary position per (player, season): prior-year argmax games, falling
-- back to the current season. Ties break on games started, then the fixed
-- position order, for determinism.
primary_candidates as (
    select
        p.mlbam_id,
        p.season_year + 1 as season_year,
        p.cbs_position,
        1                 as pref,
        p.games,
        p.games_started
    from pos_season_games p
    inner join player_seasons u
        on p.mlbam_id = u.mlbam_id
        and p.season_year + 1 = u.season_year

    union all

    select
        mlbam_id,
        season_year,
        cbs_position,
        2 as pref,
        games,
        games_started
    from pos_season_games
),

primary_pos as (
    select mlbam_id, season_year, cbs_position
    from primary_candidates
    where games > 0
    qualify row_number() over (
        partition by mlbam_id, season_year
        order by pref,
                 games desc,
                 games_started desc,
                 decode(cbs_position, 'C', 1, '1B', 2, '2B', 3, '3B', 4,
                        'SS', 5, 'OF', 6, 'DH', 7, 'P', 8, 9)
    ) = 1
),

-- The three window sources, in one shape.
windows_raw as (
    select
        mlbam_id,
        season_year,
        cbs_position,
        date_from_parts(season_year, 1, 1) as eligible_from,
        'primary'                          as eligibility_source
    from primary_pos

    union all

    select
        p.mlbam_id,
        p.season_year + 1,
        p.cbs_position,
        date_from_parts(p.season_year + 1, 1, 1),
        'prior_year_20'
    from pos_season_games p
    inner join player_seasons u
        on p.mlbam_id = u.mlbam_id
        and p.season_year + 1 = u.season_year
    where p.games >= 20

    union all

    -- The 10th distinct game at the position this season, dated. DISTINCT
    -- game_pk first: LF-then-CF mid-game is one OF game, and a position
    -- player's mop-up P inning rides in from both group files.
    select
        mlbam_id,
        season_year,
        cbs_position,
        game_date,
        'in_season_10'
    from (
        select
            mlbam_id, season_year, cbs_position, game_date, game_pk,
            row_number() over (
                partition by mlbam_id, season_year, cbs_position
                order by game_date, game_pk
            ) as game_no
        from (
            select distinct mlbam_id, season_year, cbs_position, game_date, game_pk
            from {{ ref('stg_mlb__game_positions') }}
            where cbs_position is not null
        )
    )
    where game_no = 10
)

select
    lg.league_key,
    w.mlbam_id,
    w.season_year,
    w.cbs_position,
    w.eligible_from,
    w.eligibility_source
from windows_raw w
cross join leagues lg
qualify row_number() over (
    partition by lg.league_key, w.mlbam_id, w.season_year, w.cbs_position
    order by w.eligible_from,
             decode(w.eligibility_source, 'primary', 1, 'prior_year_20', 2, 'in_season_10', 3)
) = 1
