-- int_league_season_closure.sql
-- Is this league-season FINISHED, and how do we know? One row per
-- (league_key, season_year), with the evidence named rather than implied.
--
-- WHY ONE MODEL. Two consumers ask this question and they used to answer it
-- separately: the matchup ledger needed "may I count results from this
-- season", the season-points ledger needed "is this total final". Two
-- answers to one question drift, and the first version of each drifted in
-- the same direction -- toward saying yes.
--
-- ==========================================================================
-- THE EVIDENCE, IN PRECEDENCE ORDER
-- ==========================================================================
--   1. SCHEDULE CAPTURE. Where the opt-in capture reached this season, it is
--      the live pointer and it decides -- both ways. A captured season that
--      reports itself unfinished IS unfinished, and no other evidence
--      overrides that: stale final ranks must not resurrect a season ESPN
--      says is still being played.
--   2. AUTHORITATIVE FINAL STANDINGS, consulted only where there is no
--      capture. Two shapes, both meaning "the platform has published a final
--      answer": a delivered final rank (ESPN nulls rankCalculatedFinal at 0
--      for every team in a season that has not finished, so a non-null one is
--      proof), and a parsed final-standings source, which is final by
--      construction.
--   3. SUPERSESSION, last. A season the league has since played past is
--      over. This is a statement about the league's own timeline rather than
--      a guess about the calendar, which is what makes it safe for history
--      and still refuses the present.
--   4. UNPROVEN. Nothing above answered. The latest season of a league whose
--      schedule capture has never run lands here, and that is the whole point
--      -- see below.
--
-- ==========================================================================
-- UNPROVEN MEANS NO, AND THAT IS A CHANGE
-- ==========================================================================
-- The first version of the rivalry ledger treated "no capture" as "historical
-- season, keep everything", which quietly meant a league that had never run
-- the schedule extract counted THIS TUESDAY'S running score as a win. The
-- retention rule is not about whether we captured a season; it is about
-- whether the season is over. Absence of evidence is not evidence of
-- completion.
--
-- has_schedule_capture is read from stg_matchup_schedule and NOT from the
-- derived period evidence, which is the same trap one layer down: a capture
-- that is present but malformed produces zero evidence rows, and a consumer
-- keying on the evidence would read that as "never captured" and fail OPEN on
-- exactly the season whose payload we could not understand. Capture presence
-- is a fact about the snapshot; readability is a separate fact about it.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year).
-- ==========================================================================
{{ config(materialized='view') }}

with schedule_capture as (
    -- Presence, from the snapshot table itself. A season with a row here has
    -- been captured whether or not anything downstream could parse it.
    select
        league_key,
        season_year,
        {{ boolor_agg('season_is_complete') }} as schedule_says_complete
    from {{ ref('stg_matchup_schedule') }}
    group by league_key, season_year
),

delivered_final as (
    -- A non-null final rank is ESPN's own statement that the season finished:
    -- it serves rankCalculatedFinal = 0 for every team in a season that has
    -- not, and staging nulls the zero.
    select
        league_key,
        season_year,
        count(final_rank) > 0 as has_final_rank
    from {{ ref('stg_team_standings') }}
    group by league_key, season_year
),

parsed_final as (
    -- Reconstructed year-end standings pages. The source IS the final
    -- standing, so presence is the proof.
    select distinct
        league_key,
        season_year,
        true as has_final_standings
    from {{ ref('stg_cbs__ui_standings') }}
),

-- Every season any of this league's sources has seen, which is what
-- supersession is measured against.
season_universe as (
    select league_key, season_year from schedule_capture
    union
    select league_key, season_year from delivered_final
    union
    select league_key, season_year from parsed_final
    union
    select league_key, season_year from {{ ref('stg_cbs__standings') }}
),

horizon as (
    select
        league_key,
        max(season_year) as latest_season
    from season_universe
    group by league_key
),

joined as (
    select
        u.league_key,
        u.season_year,
        (sc.league_key is not null)                     as has_schedule_capture,
        coalesce(sc.schedule_says_complete, false)      as schedule_says_complete,
        coalesce(df.has_final_rank, false)              as has_final_rank,
        coalesce(pf.has_final_standings, false)         as has_final_standings,
        (u.season_year < h.latest_season)               as is_superseded
    from season_universe u
    join horizon h
        on u.league_key = h.league_key
    left join schedule_capture sc
        on u.league_key = sc.league_key
        and u.season_year = sc.season_year
    left join delivered_final df
        on u.league_key = df.league_key
        and u.season_year = df.season_year
    left join parsed_final pf
        on u.league_key = pf.league_key
        and u.season_year = pf.season_year
)

select
    league_key,
    season_year,
    has_schedule_capture,
    schedule_says_complete,
    has_final_rank,
    has_final_standings,
    is_superseded,

    -- Non-null on purpose: "not proven complete" and "unknown" are the same
    -- thing to every consumer, and a NULL here would push a three-valued test
    -- onto each of them.
    case
        when has_schedule_capture then schedule_says_complete
        when has_final_rank or has_final_standings then true
        else is_superseded
    end as is_season_complete,

    -- WHY the answer is what it is. The audit handle for "is this season
    -- complete because we measured it, because the platform published a final
    -- answer, or only because the league moved past it?"
    case
        when has_schedule_capture then 'schedule_capture'
        when has_final_rank then 'delivered_final_rank'
        when has_final_standings then 'parsed_final_standings'
        when is_superseded then 'superseded_season'
        else 'unproven'
    end as completion_evidence

from joined
