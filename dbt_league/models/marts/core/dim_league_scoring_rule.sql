-- dim_league_scoring_rule.sql
-- "Which stats does this league score, and at what weight" -- ONE model for
-- both platforms (MLB-263, ledger S-30).
--
-- WHY. Until now the question had no shared home: the ESPN readers joined
-- dim_stat to stg_scoring_settings by stat_name, the CBS reader joined
-- dim_stat to stg_cbs__scoring_settings by canonical_key, and each path
-- rebuilt "scored or not" from whether the join hit. This dim is that join,
-- done once, over the WHOLE stat vocabulary: one row per (league, stat in
-- dim_stat), scored or not, so a consumer asks the same question of either
-- platform and reads the same two columns.
--
-- KEY. (league_key, stat_name). stat_name is the vocabulary's primary key
-- and both feeds resolve to it: ESPN's settings name it directly; CBS's
-- staging resolves cbs_key -> canonical_key -> stat_name through the seeds
-- (0 unresolved when this landed). canonical_key rides along as an
-- attribute -- it is NOT the key, because the vocabulary does not carry one
-- for every stat (ESPN's cycle stat, 30, has none), and a NULL key would
-- silently drop a scored stat.
--
-- SEASON. Both staging feeds are already "the league's current settings"
-- (each keeps the latest captured season, exposed as settings_season), so
-- this dim is one row per league x stat, not per season. If a feed ever
-- carries a settings history, this is the model that grows a season grain.
--
-- points_per_unit is NULL when the league does not score the stat;
-- is_scored says so as a boolean rather than leaving the reader to infer
-- it from the NULL. platform_stat_key is the platform's own identifier for
-- the rule (ESPN's numeric stat id, CBS's feed key), kept for tracing.
--
-- Materialization: view. Two small feeds over a ~100-row vocabulary.

{{ config(materialized='view') }}

with leagues as (
    select league_key from {{ ref('stg_scoring_settings') }}
    union
    select league_key from {{ ref('stg_cbs__scoring_settings') }}
),

vocabulary as (
    select stat_name, canonical_key, stat_category
    from {{ ref('dim_stat') }}
),

espn_rules as (
    select
        league_key,
        stat_name,
        settings_season,
        cast(espn_stat_id as varchar) as platform_stat_key,
        points_per_unit
    from {{ ref('stg_scoring_settings') }}
),

cbs_rules as (
    select
        league_key,
        stat_name,
        settings_season,
        cbs_key                       as platform_stat_key,
        points_per_unit
    from {{ ref('stg_cbs__scoring_settings') }}
    where stat_name is not null
),

rules as (
    select * from espn_rules
    union all
    select * from cbs_rules
)

select
    l.league_key,
    v.stat_name,
    v.canonical_key,
    v.stat_category,
    r.settings_season,
    r.platform_stat_key,
    r.points_per_unit,
    (r.points_per_unit is not null) as is_scored
from leagues l
cross join vocabulary v
left join rules r
    on r.league_key = l.league_key
    and r.stat_name = v.stat_name
