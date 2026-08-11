-- mart_franchise_head_to_head.sql
-- The rivalry ledger (MLB-229 rung 1). One row per ORDERED pair of canonical
-- franchises: how the row franchise has fared against the opponent franchise
-- across every completed meeting in the league's history.
--
-- LONG, NOT WIDE, DELIBERATELY. The consumer surface is a matrix, but a matrix
-- is a RENDER, not a grain. A wide table needs one column per franchise, so its
-- shape changes every time a league gains or loses one -- which means dynamic
-- SQL, a schema that cannot be documented or tested, and a contract no
-- downstream reader can join to. Long format keeps the grain stable at
-- (league_key, row, opponent) and leaves the pivot to whoever draws the grid.
--
-- ==========================================================================
-- IDENTITY: FRANCHISES MEET, TEAM-SEASONS PLAY
-- ==========================================================================
-- The matchup facts keep their real platform team ids and are never rewritten.
-- Resolution happens HERE, at read time, through dim_franchise_season -- the
-- season-aware lineage machinery (MLB-64 / MLB-113 / MLB-115). That matters in
-- three ways this model is tested against:
--
--   * A franchise that left and came back under a NEW platform id (Foster's
--     Folly, 13 through 2019 and 30 from 2021) is ONE rival, and its record
--     against a third franchise spans both eras.
--   * A LIVE id handed to a new manager mid-history is TWO franchises, and the
--     season-scoped lineage row is the only thing that can say so -- which is
--     why this joins the season-grain dim rather than the franchise-grain one.
--   * Two unrelated franchises that happen to share a display name stay
--     separate rows, because aggregation keys on canonical_franchise_id and
--     never on name text. Name is a LABEL here, resolved after the group by.
--
-- DISPLAY follows the house rule (Kyle, 2026-07-22): the configured
-- canonical_name when the lineage seed gives one, else the resolved
-- franchise's latest observed platform name. Both already live in
-- dim_franchise.canonical_name, so this model reads that column rather than
-- re-deriving the precedence and inviting the two to drift.
--
-- ==========================================================================
-- WHAT COUNTS AS A MEETING
-- ==========================================================================
-- Source is mart_team_matchup -- the established matchup-grain surface, the
-- same one mart_team_season_standings rolls W-L-T out of. It is already
-- filtered to team-weeks WITH an opponent, so byes never arrive here; the
-- explicit bye guard below is kept anyway because this model's correctness
-- must not depend on an upstream WHERE clause staying put.
--
-- A meeting is counted when ALL of these hold:
--   1. there is an opponent (not a bye),
--   2. the two sides are different platform teams,
--   3. BOTH sides carry a platform score.
--
-- (3) IS LOAD-BEARING, NOT DEFENSIVE. The fact derives `result` as
-- W / L / else 'T'. A NULL on either side makes both comparisons NULL and
-- falls through to the else -- so an unscored week would enter a rivalry
-- ledger as a TIE. Requiring both scores is what stops a capture gap from
-- minting ties, and it is also what makes the filter SYMMETRIC: one team's
-- platform_points is the other's opponent_points, so a pair is either kept
-- from both directions or dropped from both. Reciprocity is a consequence of
-- that symmetry, and assert_head_to_head_reciprocity holds it.
--
-- PLATFORM LENS THROUGHOUT. points_for / points_against are platform_points,
-- the same lens `result` is derived from -- ESPN's authoritative team total,
-- slot-aware and inclusive of commissioner adjustments. Mixing lenses would
-- let a franchise show more points_for than points_against while holding a
-- losing record. mart_team_matchup also carries the calculated lens, so a
-- cross-season-comparable variant is a later addition, not a rewrite.
--
-- PLAYOFFS ARE IN. A standings is a regular-season object and
-- mart_team_season_standings rightly excludes them; a RIVALRY is not. Two
-- franchises that met in a final met, and a ledger that silently drops the
-- biggest game either played is telling a shorter story than it claims to.
-- is_playoff rides on the fact, so a regular-season-only variant is one
-- predicate away if Kyle wants the standings reading instead.
--
-- ABNORMAL WEEKS ARE IN. is_record_eligible gates the RECORD BOOK, where an
-- odd-length week distorts a per-week extreme. A head-to-head result is not an
-- extreme -- a win in a 10-day opening week is a win -- so filtering here
-- would discard real meetings to answer a question nobody asked of it.
--
-- THE HOLDING PEN IS OUT, ON BOTH SIDES. The pen (holding_pen_franchise_id) is
-- not a franchise; it catches production attributable to a league but to no
-- team, and every team-grain aggregation fences it out (MLB-115). A
-- season-scoped lineage row can park a real team-season on it -- that is
-- exactly what the row saying a season was unowned MEANS -- and those meetings
-- leave the ledger from both directions, which is why the exclusion sits
-- before the aggregation rather than after it.
--
-- NO ACTIVITY FILTER. Every franchise that has ever completed a meeting is
-- here, defunct or not. The project has no authoritative definition of "still
-- active" -- the two implementations that exist disagree in mechanism and both
-- read live roster captures -- so this model does not invent one. See
-- docs/decisions/HEAD_TO_HEAD_RIVALS_CONTRACT.md for the exact open choices.
--
-- THE DIAGONAL IS ABSENT, NOT ZERO. A franchise has no row against itself, so
-- a renderer draws an empty cell rather than a 0-0 that reads as a played
-- series. Self-pairs are removed AFTER resolution, not before: two different
-- platform ids resolving to one canonical franchise in the same season is the
-- case a raw team_id <> opponent_id check cannot see.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, row_canonical_franchise_id,
--                     opponent_canonical_franchise_id).
-- ==========================================================================
-- Materialization: table. Float score sums (MLB-128 discipline applies), and
-- a view would re-sum them in a nondeterministic order on every read.

{{ config(materialized='table') }}

with meetings as (
    select
        league_key,
        season_year,
        matchup_period,
        team_id,
        opponent_id,
        platform_points,
        opponent_points,
        result
    from {{ ref('mart_team_matchup') }}
    where opponent_id is not null
      and team_id <> opponent_id
      and platform_points is not null
      and opponent_points is not null
      and result is not null
),

-- Each side of the meeting resolved through the SEASON-aware dim, keyed by
-- the season the meeting was played in. Inner joins: a team-season the
-- lineage spine cannot speak about is dropped rather than guessed at, and it
-- is dropped symmetrically (the same missing team-season fails the row join
-- on one direction and the opponent join on the other).
-- assert_head_to_head_identity_resolves proves the drop set is empty.
resolved as (
    select
        m.league_key,
        m.season_year,
        row_f.canonical_franchise_id      as row_canonical_franchise_id,
        opp_f.canonical_franchise_id      as opponent_canonical_franchise_id,
        m.platform_points,
        m.opponent_points,
        m.result
    from meetings m
    join {{ ref('dim_franchise_season') }} row_f
        on m.league_key = row_f.league_key
        and cast(m.team_id as varchar) = row_f.franchise_id
        and m.season_year = row_f.season_year
    join {{ ref('dim_franchise_season') }} opp_f
        on m.league_key = opp_f.league_key
        and cast(m.opponent_id as varchar) = opp_f.franchise_id
        and m.season_year = opp_f.season_year
),

countable as (
    select *
    from resolved
    where row_canonical_franchise_id <> opponent_canonical_franchise_id
      and row_canonical_franchise_id
          <> '{{ var("holding_pen_franchise_id") }}'
      and opponent_canonical_franchise_id
          <> '{{ var("holding_pen_franchise_id") }}'
),

aggregated as (
    select
        league_key,
        row_canonical_franchise_id,
        opponent_canonical_franchise_id,

        count(*) as meetings,
        sum(case when result = 'W' then 1 else 0 end) as wins,
        sum(case when result = 'L' then 1 else 0 end) as losses,
        sum(case when result = 'T' then 1 else 0 end) as ties,

        -- Exact-decimal summation (MLB-128): float addition is not
        -- associative, so a plain sum can move between rebuilds with no data
        -- change.
        {{ stable_sum("platform_points") }} as points_for,
        {{ stable_sum("opponent_points") }} as points_against,

        -- Margin subtracts the two sums IN DECIMAL rather than differencing
        -- the doubles above. Both inputs are exact at this scale, so the
        -- difference is exact too -- differencing the cast-back doubles would
        -- reintroduce the representation error the decimal sum just removed
        -- (the classic 0.1 + 0.2 tail), and margin is the column a reciprocity
        -- test compares against its own negation.
        cast(round(
            sum(cast(platform_points as decimal(18, 6)))
            - sum(cast(opponent_points as decimal(18, 6)))
        , 1) as double) as points_margin,

        min(season_year) as first_meeting_season,
        max(season_year) as last_meeting_season

    from countable
    group by 1, 2, 3
),

-- One row per canonical franchise. dim_franchise is franchise-grain and every
-- member of a lineage carries the same canonical labels, so the ANCHOR row is
-- the whole lookup -- no dedupe, no aggregate over a name.
franchise_display as (
    select
        league_key,
        franchise_id as canonical_franchise_id,
        canonical_name,
        canonical_abbrev
    from {{ ref('dim_franchise') }}
    where is_canonical_anchor
)

select
    a.league_key,
    a.row_canonical_franchise_id,
    a.opponent_canonical_franchise_id,

    row_d.canonical_name    as row_franchise_name,
    row_d.canonical_abbrev  as row_franchise_abbrev,
    opp_d.canonical_name    as opponent_franchise_name,
    opp_d.canonical_abbrev  as opponent_franchise_abbrev,

    a.meetings,
    a.wins,
    a.losses,
    a.ties,

    a.points_for,
    a.points_against,
    a.points_margin,

    -- The project's own winning percentage, lifted from the ordering in
    -- output/almanac_data.py:get_team_alltime_stats rather than invented here:
    -- a tie is half a win. Integer arithmetic over a count, so it carries none
    -- of the float-summation order dependence the point columns do, and it is
    -- left unrounded -- display precision belongs to the renderer.
    (a.wins + 0.5 * a.ties) / nullif(a.meetings, 0) as win_pct,

    a.first_meeting_season,
    a.last_meeting_season

from aggregated a
join franchise_display row_d
    on a.league_key = row_d.league_key
    and a.row_canonical_franchise_id = row_d.canonical_franchise_id
join franchise_display opp_d
    on a.league_key = opp_d.league_key
    and a.opponent_canonical_franchise_id = opp_d.canonical_franchise_id
