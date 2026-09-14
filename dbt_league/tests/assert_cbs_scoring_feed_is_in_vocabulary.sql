-- Singular test: every category a CBS league SCORES is a category the
-- cbs_stat_map seed MAPS (MLB-222, findings SC-01/SC-02/SC-03).
--
-- The sibling of assert_espn_scoring_feed_is_in_vocabulary, for the other
-- inner join: stg_cbs__scoring_settings keeps only feed categories whose
-- cbs_stat_map disposition is 'mapped', so a scored category the seed does
-- not map -- or maps to nothing -- vanishes from the staged rules and from
-- every calculated_ total, silently.
--
-- This is NOT assert_cbs_scoring_feed_matches_seed. That one grades ONE
-- curated league's feed against the seed's hand-curated weights (a
-- documentation-drift check, scoped by cbs_curated_scoring_league). This
-- one asks a question that is true for every CBS league: whatever you
-- score, can we name it? It reads every league's latest scoring_rules
-- capture, is vacuous when there is none (an ESPN-only install), and is
-- green on cbs-bsb (16 feed categories, 16 mapped).
--
-- Returns one row per unmapped scored category; zero rows = pass.

with latest_season as (
    select league_key, max(season_year) as season_year
    from {{ source('raw', 'cbs_config') }}
    where config_kind = 'scoring_rules'
    group by league_key
),

latest_snapshot as (
    select c.league_key, c.season_year, c.payload
    from {{ source('raw', 'cbs_config') }} c
    inner join latest_season ls
        on c.league_key = ls.league_key
        and c.season_year = ls.season_year
    where c.config_kind = 'scoring_rules'
    qualify row_number() over (
        partition by c.league_key, c.season_year
        order by c.loaded_at desc, c.captured_at desc, hash(c.payload) desc
    ) = 1
),

scored as (
    select
        s.league_key,
        s.season_year,
        {{ json_text('f.value', 'name') }}::string   as cbs_key,
        {{ json_text('f.value', 'group') }}::string  as rules_group,
        {{ json_text('f.value', 'points') }}::double as points
    from latest_snapshot s,
        {{ flatten_array(json_get('s.payload', 'body', 'scoring_rules', 'categories'), 'f') }}
)

select
    s.league_key,
    s.season_year,
    s.cbs_key,
    s.rules_group,
    s.points,
    case
        when m.cbs_key is null then 'scored category has no row in seeds/cbs_stat_map.csv'
        else 'scored category is in the seed but its disposition is not mapped'
    end || ' -- its points are dropped from every calculated_ total' as failed_invariant
from scored s
left join {{ ref('cbs_stat_map') }} m
    on m.cbs_key = s.cbs_key
where m.cbs_key is null
   or m.disposition != 'mapped'
