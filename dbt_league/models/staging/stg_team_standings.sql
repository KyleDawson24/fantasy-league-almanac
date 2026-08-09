-- stg_team_standings.sql
-- Per-team, per-season standings as ESPN itself reports them, flattened from
-- the append-only RAW.TEAM_STANDINGS snapshots (MLB-227).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id).
-- ==========================================================================
--
-- WHY THIS EXISTS AT ALL: the platform's own seeding was being reconstructed
-- downstream with a flat `order by wins desc, ties desc, points desc`, and
-- that reconstruction is provably wrong. ESPN seeds DIVISION WINNERS FIRST,
-- then the rest of the field by record. 2026 is the counterexample: SMEL
-- (14-3, Beer League leader) seeds 1, HH (11-6, Spitballs leader) seeds 2,
-- and CYCL (13-4, best non-leader) seeds only 3 -- a flat record sort swaps
-- the last two. The remaining eleven seeds then match record order exactly,
-- which is what makes it the rule rather than a coincidence; 2025 reproduces
-- it with four divisions, where seeds 1-4 are precisely the four division
-- leaders. The fix is not a better sort. It is reading the number ESPN
-- already computed, which is what this model surfaces.
--
-- PLAYOFF SEED AND FINAL RANK ARE DIFFERENT QUESTIONS and both are kept:
--   playoff_seed  -- the REGULAR-SEASON standing, 1..N, assigned to every
--                    team including non-qualifiers. It is not a
--                    bracket-membership flag; compare it against
--                    playoff_team_count to get that.
--   final_rank    -- the POST-PLAYOFF finish.
-- They routinely disagree. In 2025 the 7 seed (FUBB) finished 1st, the 1
-- seed (DPRK) finished 2nd, the 2 seed (CYCL) finished 6th.
--
-- ZERO MEANS UNASSIGNED, NOT A RANK. ESPN serves rankCalculatedFinal = 0 for
-- every team in a season that has not finished (all of 2026 today), and a
-- closed season still serves its real ranks on re-fetch, so this backfills.
-- Both rank columns are nulled at 0 so "no answer yet" cannot be mistaken
-- for "finished ahead of first place" in a sort or an average.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'team_standings') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order; see stg_schedule_settings for the reason.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
),

teams as (
    select
        le.league_key,
        le.season_year,
        t.value as team
    from latest_extraction le,
        {{ flatten_array('le.raw_json', 't') }}
)

select
    league_key,
    season_year,
    {{ json_text('team', 'id') }}::integer                 as team_id,
    {{ json_text('team', 'abbrev') }}::string              as team_abbrev,
    -- Team names are user data and can be anything -- numeric-looking
    -- strings, emoji, sentinels. Carried through verbatim as text.
    {{ json_text('team', 'name') }}::string                as team_name,
    {{ json_text('team', 'divisionId') }}::integer         as division_id,

    nullif({{ json_text('team', 'playoffSeed') }}::integer, 0)
        as playoff_seed,
    nullif({{ json_text('team', 'rankCalculatedFinal') }}::integer, 0)
        as final_rank,

    {{ json_text('team', 'record', 'overall', 'wins') }}::integer    as wins,
    {{ json_text('team', 'record', 'overall', 'losses') }}::integer  as losses,
    {{ json_text('team', 'record', 'overall', 'ties') }}::integer    as ties,
    {{ json_text('team', 'record', 'overall', 'percentage') }}::double
        as win_percentage,
    {{ json_text('team', 'record', 'overall', 'gamesBack') }}::double
        as games_back,
    {{ json_text('team', 'record', 'overall', 'streakLength') }}::integer
        as streak_length,
    {{ json_text('team', 'record', 'overall', 'streakType') }}::string
        as streak_type,

    {{ json_text('team', 'record', 'division', 'wins') }}::integer   as division_wins,
    {{ json_text('team', 'record', 'division', 'losses') }}::integer as division_losses,
    {{ json_text('team', 'record', 'division', 'ties') }}::integer   as division_ties,

    -- ESPN's own season point total for the team. NOT the warehouse's
    -- calculated total and not asserted to equal it -- the platform's point
    -- stats have diverged before (the Week-12 two-way case), so this stays
    -- labelled as the platform's number and reconciliation stays a separate
    -- question.
    {{ json_text('team', 'points') }}::double                  as platform_points,
    {{ json_text('team', 'pointsAdjusted') }}::double          as platform_points_adjusted,
    {{ json_text('team', 'waiverRank') }}::integer             as waiver_rank
from teams
