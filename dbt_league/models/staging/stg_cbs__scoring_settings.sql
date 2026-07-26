-- stg_cbs__scoring_settings.sql
-- CBS adapter (#2) scoring rules, one row per scored category -- the F4
-- weights that price the calculated_ lens (MLB-62). Mirrors
-- stg_scoring_settings (ESPN): read the platform's own rules feed from RAW,
-- surface only the CURRENT season's weights (max season_year with a
-- snapshot, latest snapshot wins), apply them universally -- including to
-- historical stats -- so cross-season scores are comparable.
--
-- Source: raw.cbs_config, config_kind = 'scoring_rules'. The payload's
-- categories[] carries {name (the CBS stat key), group Batting|Pitching,
-- points} -- 16 categories, no bonuses/ranges in this league. The
-- cbs_stat_map seed bridges cbs_key -> canonical_key; the seed's
-- points_2026 column mirrors this feed and a singular test
-- (assert_cbs_scoring_feed_matches_seed) keeps the two in lockstep.
--
-- THE INN TRANSLATION (the one place scoring departs from the feed's
-- surface): CBS lists INN at 3 points per inning, but the platform PAYS at
-- out-granularity -- 1 point per out recorded, verified 587/587 against
-- 2025 season FPTS via OUTS where floor(INN) reconciles only 559/587. The
-- feed's INN row therefore lands here as canonical_key 'outs_recorded' at
-- points_per_unit = points/3, and the verbatim feed values ride along in
-- cbs_key/feed_points. Downstream engines multiply outs, never innings.
--
-- QS and IRSTR pass through as their canonicals (quality_starts,
-- inherited_runners_stranded) -- neither exists as a statsapi feed key, so
-- the points engine DERIVES the per-game values (QS: started AND outs>=18
-- AND ER<=3; IRSTR: inherited_runners - inherited_runners_scored) and
-- prices them with these weights.
--
-- Grain: one row per (league_key, cbs_key) -- a per-league single-season
-- reference table representing "the league's current rules."

with latest_season as (
    select
        league_key,
        max(season_year) as season_year
    from {{ source('raw', 'cbs_config') }}
    where config_kind = 'scoring_rules'
    group by league_key
),

latest_snapshot as (
    select
        c.league_key,
        c.season_year,
        c.payload
    from {{ source('raw', 'cbs_config') }} c
    inner join latest_season ls
        on c.league_key = ls.league_key
        and c.season_year = ls.season_year
    where c.config_kind = 'scoring_rules'
    qualify row_number() over (
        partition by c.league_key, c.season_year
        order by c.loaded_at desc
    ) = 1
),

categories as (
    select
        s.league_key,
        s.season_year                as settings_season,
        f.value:name::string         as cbs_key,
        f.value:group::string        as rules_group,
        f.value:points::double        as feed_points
    from latest_snapshot s,
        lateral flatten(input => s.payload:body:scoring_rules:categories) f
)

select
    c.league_key,
    c.settings_season,
    c.cbs_key,
    c.rules_group,
    -- The scoring primitive: what a points engine multiplies. INN is the
    -- one translated row (see header); everything else is the seed bridge.
    case when c.cbs_key = 'INN' then 'outs_recorded'
         else m.canonical_key end                       as canonical_key,
    -- ESPN-namespace stat name where the vocabulary bridges (convergence
    -- consumers); NULL where it doesn't.
    sc.stat_name,
    m.stat_category,
    case when c.cbs_key = 'INN' then c.feed_points / 3.0
         else c.feed_points end                         as points_per_unit,
    c.feed_points
from categories c
inner join {{ ref('cbs_stat_map') }} m
    on c.cbs_key = m.cbs_key
    and m.disposition = 'mapped'
left join {{ ref('stat_classification') }} sc
    on sc.canonical_key = case when c.cbs_key = 'INN' then 'outs_recorded'
                               else m.canonical_key end
