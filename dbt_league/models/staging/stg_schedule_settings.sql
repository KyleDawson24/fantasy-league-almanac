-- stg_schedule_settings.sql
-- The league's per-season schedule and playoff structure, flattened from the
-- append-only RAW.SCHEDULE_SETTINGS snapshots (MLB-227).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year).
-- ==========================================================================
--
-- EVERY SEASON IS KEPT, which is the difference between this and
-- stg_scoring_settings. Scoring weights are deliberately collapsed to the
-- current season's so cross-season point totals share one scale; schedule
-- shape is the opposite -- the league genuinely was 16 teams in 4 divisions
-- in 2025 and 14 teams in 2 divisions in 2026, and flattening that to "the
-- current shape" would misdescribe every historical standing. Anything keyed
-- on a season-invariant league shape is wrong here.
--
-- total_matchup_periods vs matchup_period_count: the COUNT is the regular
-- season (22 in 2026), the TOTAL is every period ESPN schedules including
-- playoff rounds (25). The total is only available as the key count of the
-- matchupPeriods object, which is why it is read that way rather than from a
-- scalar -- there is no scalar for it.
--
-- WHAT matchupPeriods CANNOT TELL YOU, recorded here because the shape
-- invites the assumption: it maps every matchup period to a single-element
-- list ({'1': [1], '2': [2], ...}) in ESPN's own payload, not as an artifact
-- of the espn-api wrapper -- verified against the raw HTTP response for both
-- seasons. It therefore carries no period membership, no start/end dates and
-- no per-period length, and matchup_period_length is a flat scalar (1) that
-- is wrong for the two long periods every season actually has. The
-- matchup_schedule seed remains the only source of real period boundaries.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'schedule_settings') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order. extracted_at alone ties whenever one
        -- extract stamps two payload versions of the same entity (a re-run
        -- or a double-capture). RAW carries no load sequence id, so the
        -- payload hash is the only discriminator available; it can only ever
        -- choose between byte-identical payloads, which makes the VALUE
        -- deterministic even though the row choice is arbitrary.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
)

select
    league_key,
    season_year,
    {{ json_text('raw_json', 'matchupPeriodCount') }}::integer
        as regular_season_periods,
    {{ json_keys_count(json_get('raw_json', 'matchupPeriods')) }}::integer
        as total_matchup_periods,
    {{ json_text('raw_json', 'matchupPeriodLength') }}::integer
        as matchup_period_length,
    {{ json_text('raw_json', 'playoffTeamCount') }}::integer
        as playoff_team_count,
    {{ json_text('raw_json', 'playoffMatchupPeriodLength') }}::integer
        as playoff_matchup_period_length,
    {{ json_text('raw_json', 'playoffSeedingRule') }}::string
        as playoff_seeding_rule,
    {{ json_text('raw_json', 'playoffSeedingRuleBy') }}::integer
        as playoff_seeding_rule_by,
    {{ json_text('raw_json', 'playoffReseed') }}::boolean
        as playoff_reseed,
    {{ json_text('raw_json', 'variablePlayoffMatchupPeriodLength') }}::boolean
        as variable_playoff_period_length,
    {{ json_text('raw_json', 'consolationLadderDisabled') }}::boolean
        as consolation_ladder_disabled,
    {{ json_text('raw_json', 'periodTypeId') }}::integer
        as period_type_id,
    -- Derivable here and nowhere cheaper: the periods past the regular
    -- season are the playoff rounds.
    {{ json_keys_count(json_get('raw_json', 'matchupPeriods')) }}::integer
        - {{ json_text('raw_json', 'matchupPeriodCount') }}::integer
        as playoff_rounds
from latest_extraction
