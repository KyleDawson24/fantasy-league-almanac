-- Singular test: every stat an ESPN league SCORES is a stat this project
-- can NAME (MLB-222, findings SC-01/SC-02/SC-03).
--
-- stg_scoring_settings inner-joins the platform's scoring feed onto the
-- stat_classification seed. That join is the right shape for the model --
-- it is what gives every scored stat a name, a category and a canonical
-- key -- but it makes an unknown statId disappear silently: the league
-- scores it, the feed carries it, the staged rules do not, and every
-- calculated_ total downstream is short by exactly that stat's points
-- with nothing in the build to say so. The only symptom was a non-zero
-- platform_calculated_delta, which has other legitimate causes.
--
-- This test re-reads the SAME latest snapshot the model reads and lists
-- every scored statId the seed does not carry. It is vacuous when a league
-- has no ESPN scoring feed (a CBS-only install) and green on both pioneer
-- leagues (measured 2026-09-14: espn-main 35 feed items, 35 staged). A
-- stranger's league scoring a stat we have never classified fails the
-- build here, by id, instead of rendering totals that are quietly wrong --
-- the fix is a row in seeds/stat_classification.csv, which is where every
-- other stat lives.
--
-- Returns one row per unknown scored stat; zero rows = pass.

with latest_season as (
    select league_key, max(season_year) as season_year
    from {{ source('raw', 'scoring_settings') }}
    group by league_key
),

latest_extraction as (
    select s.league_key, s.season_year, s.raw_json
    from {{ source('raw', 'scoring_settings') }} s
    inner join latest_season ls
        on s.league_key = ls.league_key
        and s.season_year = ls.season_year
    qualify row_number() over (
        partition by s.league_key, s.season_year
        order by s.extracted_at desc, hash(s.raw_json) desc
    ) = 1
),

scored as (
    select
        e.league_key,
        e.season_year,
        {{ json_text('f.value', 'statId') }}::integer as espn_stat_id,
        {{ json_text('f.value', 'points') }}::double  as points
    from latest_extraction e,
        {{ flatten_array('e.raw_json', 'f') }}
)

select
    s.league_key,
    s.season_year,
    s.espn_stat_id,
    s.points,
    'scored statId has no row in seeds/stat_classification.csv -- '
    || 'its points are dropped from every calculated_ total' as failed_invariant
from scored s
left join {{ ref('stat_classification') }} sc
    on sc.espn_stat_id = s.espn_stat_id
where sc.espn_stat_id is null
