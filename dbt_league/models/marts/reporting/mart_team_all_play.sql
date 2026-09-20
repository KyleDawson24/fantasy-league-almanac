-- mart_team_all_play.sql
-- All-play (MLB-301): what every team's PLATFORM score would have done
-- against every other team's in the same completed regular-season
-- matchup period. Promoted from scratchpad/sql/season-wrap-2026.sql
-- section 2, which lived as a one-off wrap-up query, into a contract the
-- shared almanac export and the browser read from one place.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, matchup_period, team_id) --
-- one team-matchup, carrying that team's pairwise record against the rest
-- of the field THAT period.
-- ==========================================================================
--
-- THE SCORE IS THE PLATFORM'S. The wrap-up compared the platform's own
-- per-matchup team totals (commissioner adjustments included), not the
-- calculated lens and not the scheduled opponent's result, and this mart
-- keeps that: a team's `platform_points` is set against every other team's
-- `platform_points` in the period. Recomputing under the calculated lens is
-- a different metric and belongs to a different column, not a silent swap.
--
-- STRICT WINS, STRICT LOSSES, TIES COUNTED SEPARATELY. A tie is neither half
-- a win nor half a loss here (MLB-302 may rule otherwise, in which case the
-- change lands in the shared metric, not in one renderer). The three counts
-- always sum to `opponent_count`, which is exported per matchup so a league
-- whose size changed between seasons can be summed honestly: expected wins
-- over a set of matchups = SUM(all_play_wins / opponent_count), likewise
-- losses and ties, and expected win% = expected wins / matchups. In a fixed
-- 14-team league that collapses to wins / 13, but the per-matchup form is
-- what is stored so the collapse is never assumed.
--
-- COMPLETED REGULAR-SEASON MATCHUPS ONLY, GATED THE WAY THE RIVALRY LEDGER
-- IS. Playoff periods are excluded (a standings object). A period counts
-- only when int_matchup_period_evidence proves it closed for a season the
-- schedule capture reached, or when int_league_season_closure proves the
-- season finished for a season the capture never reached -- the same
-- fail-closed rule mart_franchise_rivalry applies, for the same reason: a
-- matchup in flight already carries running totals on both sides, and
-- "has a score" is not "is over". A team-week needs an opponent to be a
-- matchup at all (mart_team_matchup already excludes byes and the FA-pool
-- row) and a platform score on its side; a NULL score is unknown, not 0.
--
-- NO SELF-COMPARISONS: the pairwise join excludes a.team_id = b.team_id.
-- League-wide, every strict win is somebody else's strict loss, so the two
-- totals are reciprocal by construction (assert_all_play_is_reciprocal).
--
-- Materialization: table -- counts only, but the export and the browser read
-- it beside the float-summed standings marts and one rebuild should mean one
-- set of numbers.

{{ config(materialized='table') }}

with period_evidence as (
    select
        league_key,
        season_year,
        matchup_period,
        is_closed
    from {{ ref('int_matchup_period_evidence') }}
),

season_closure as (
    select
        league_key,
        season_year,
        has_schedule_capture,
        is_season_complete
    from {{ ref('int_league_season_closure') }}
),

-- Every completed regular-season team-matchup with a platform score.
scored as (
    select
        m.league_key,
        m.season_year,
        m.matchup_period,
        m.team_id,
        m.team_name,
        m.team_abbrev,
        m.opponent_id,
        m.platform_points,
        m.result as matchup_result
    from {{ ref('mart_team_matchup') }} m
    -- INNER: a season the closure model cannot speak about has no
    -- completion verdict, and a missing verdict is not permission.
    join season_closure sc
        on m.league_key = sc.league_key
        and m.season_year = sc.season_year
    left join period_evidence pe
        on m.league_key = pe.league_key
        and m.season_year = pe.season_year
        and m.matchup_period = pe.matchup_period
    where not m.is_playoff
      and m.platform_points is not null
      and (
            case
                -- Captured season: the PERIOD must be proven closed.
                when sc.has_schedule_capture then coalesce(pe.is_closed, false)
                -- Uncaptured season: retained only where the SEASON is
                -- independently proven finished.
                else sc.is_season_complete
            end
          )
),

-- Each team against every OTHER team in the same period.
pairs as (
    select
        a.league_key,
        a.season_year,
        a.matchup_period,
        a.team_id,
        count(*) as opponent_count,
        sum(case when a.platform_points > b.platform_points then 1 else 0 end) as all_play_wins,
        sum(case when a.platform_points < b.platform_points then 1 else 0 end) as all_play_losses,
        sum(case when a.platform_points = b.platform_points then 1 else 0 end) as all_play_ties
    from scored a
    join scored b
        on a.league_key = b.league_key
        and a.season_year = b.season_year
        and a.matchup_period = b.matchup_period
        and a.team_id <> b.team_id
    group by 1, 2, 3, 4
)

select
    s.league_key,
    s.season_year,
    s.matchup_period,
    s.team_id,
    s.team_name,
    s.team_abbrev,
    s.platform_points,
    s.opponent_id,
    s.matchup_result,
    p.opponent_count,
    p.all_play_wins,
    p.all_play_losses,
    p.all_play_ties,
    -- This matchup's contribution to an expected record: the share of the
    -- field it beat, lost to, and tied. Summed over any set of matchups
    -- these give expected wins / losses / ties for that set.
    cast(p.all_play_wins   as double) / p.opponent_count as expected_win_share,
    cast(p.all_play_losses as double) / p.opponent_count as expected_loss_share,
    cast(p.all_play_ties   as double) / p.opponent_count as expected_tie_share
from scored s
join pairs p
    on s.league_key = p.league_key
    and s.season_year = p.season_year
    and s.matchup_period = p.matchup_period
    and s.team_id = p.team_id
