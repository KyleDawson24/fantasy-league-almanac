-- stg_cbs__league_settings.sql
-- CBS adapter (#2) league-level settings -- the platform's OWN statement of
-- what kind of league this is (MLB-263, ledger S-34).
--
-- WHY IT EXISTS. dim_league_format decided format by FEED PRESENCE: CBS
-- delivers period standings only where there are no matchups, so their
-- presence implied `points`. That inference is sound for the pioneer league
-- and wrong in general -- a CBS head-to-head league would be misfiled --
-- and the contract says format is a SETTING (F4/F6), not a shape. The audit
-- asked whether RAW carries the setting; it does, so this reads it.
--
-- Source: raw.cbs_config, config_kind = 'league_rules' and 'details'.
--   body.rules.scoring_system.scoring_system  'Ranked, Points'  (the label)
--   body.rules.scoring_system.type            'ranked'
--   body.league_details.type                  'mgmt'
--   body.league_details.scoring_periods       '27'
--
-- CALIBRATED ON ONE LEAGUE, AND SAID OUT LOUD. Only cbs-bsb has a captured
-- config, so the label vocabulary is observed, not enumerated: 'Ranked,
-- Points' is the single value on file. The verdict below therefore tests
-- for head-to-head FIRST and falls through to points, and returns NULL --
-- not a guess -- for any label it does not recognise. NULL is what lets
-- dim_league_format fall back to the presence inference instead of a
-- confident wrong answer.
--
-- Config is captured for the CURRENT season only (2026 today), which is the
-- right grain: dim_league_format is one row per league_key.
--
-- Grain: one row per league_key.

with latest_season as (
    select
        league_key,
        max(season_year) as season_year
    from {{ source('raw', 'cbs_config') }}
    where config_kind in ('league_rules', 'details')
    group by league_key
),

-- Same total-order rule as stg_cbs__scoring_settings (MLB-134): loaded_at is
-- the warehousing time and a backfill stamps many captures with one value,
-- so captured_at carries the real recency and the payload hash is the final
-- backstop between byte-identical snapshots.
rules as (
    select c.league_key, c.season_year, c.payload
    from {{ source('raw', 'cbs_config') }} c
    inner join latest_season ls
        on c.league_key = ls.league_key
        and c.season_year = ls.season_year
    where c.config_kind = 'league_rules'
    qualify row_number() over (
        partition by c.league_key, c.season_year
        order by c.loaded_at desc, c.captured_at desc, hash(c.payload) desc
    ) = 1
),

details as (
    select c.league_key, c.season_year, c.payload
    from {{ source('raw', 'cbs_config') }} c
    inner join latest_season ls
        on c.league_key = ls.league_key
        and c.season_year = ls.season_year
    where c.config_kind = 'details'
    qualify row_number() over (
        partition by c.league_key, c.season_year
        order by c.loaded_at desc, c.captured_at desc, hash(c.payload) desc
    ) = 1
),

extracted as (
    select
        r.league_key,
        r.season_year                                    as settings_season,
        {{ json_text('r.payload', 'body', 'rules', 'scoring_system',
                     'scoring_system') }}::string        as scoring_system_label,
        {{ json_text('r.payload', 'body', 'rules', 'scoring_system',
                     'type') }}::string                  as scoring_system_type,
        {{ json_text('d.payload', 'body', 'league_details',
                     'type') }}::string                  as league_detail_type,
        {{ json_text('d.payload', 'body', 'league_details',
                     'scoring_periods') }}::integer      as scoring_periods
    from rules r
    left join details d
        on r.league_key = d.league_key
        and r.season_year = d.season_year
)

select
    league_key,
    settings_season,
    scoring_system_label,
    scoring_system_type,
    league_detail_type,
    scoring_periods,

    -- THE SETTINGS-DERIVED VERDICT. Head-to-head is tested first because a
    -- CBS H2H league still scores in points and would otherwise match the
    -- points branch on the word alone. An unrecognised label yields NULL so
    -- the consumer can fall back rather than inherit a guess.
    case
        when lower(scoring_system_label) like '%head%'   then 'h2h'
        when lower(scoring_system_label) like '%points%' then 'points'
    end as settings_league_format
from extracted
