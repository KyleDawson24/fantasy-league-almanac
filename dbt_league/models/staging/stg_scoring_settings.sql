-- stg_scoring_settings.sql
-- Reshape raw scoring settings into one row per scored stat.
-- Surfaces only the CURRENT season's weights (max season_year with data).
--
-- Historical settings are preserved in RAW.SCORING_SETTINGS (append-only,
-- timestamped) but not surfaced here. The current season's weights are
-- applied universally -- including to historical stat breakdowns -- so that
-- cross-season scores are comparable under a common scale.
--
-- When multiple extractions exist for the same season, picks the most
-- recent via ROW_NUMBER() on extracted_at. This follows the ELT principle:
-- extraction captures every snapshot, staging picks the right one.
--
-- Grain: one row per (league_key, stat_name) (no season_year dimension --
-- it's a per-league single-season reference table representing "the
-- league's current rules"). "Latest season" resolves independently per
-- league: one league mid-backfill can't drag another off its current
-- weights.

with latest_season as (
    select
        league_key,
        max(season_year) as season_year
    from {{ source('raw', 'scoring_settings') }}
    group by league_key
),

latest_extraction as (
    select
        s.league_key,
        s.season_year,
        s.raw_json
    from {{ source('raw', 'scoring_settings') }} s
    inner join latest_season ls
        on s.league_key = ls.league_key
        and s.season_year = ls.season_year
    qualify row_number() over (
        partition by s.league_key, s.season_year
        -- MLB-134 -- total order. extracted_at alone ties whenever one
        -- extract stamps two payload versions of the same entity (a re-run
        -- or a double-capture). RAW carries no load sequence id, so the
        -- payload hash is the only discriminator available; it can only ever
        -- choose between byte-identical payloads, which makes the VALUE
        -- deterministic even though the row choice is arbitrary.
        order by s.extracted_at desc, hash(s.raw_json) desc
    ) = 1
),

flattened as (
    -- ESPN stores penalty stats (errors, earned runs allowed, walks allowed, etc.)
    -- with positive `points` magnitude AND isReverseItem=true. The reverse flag
    -- flips the sign at scoring time. We apply it here so points_per_unit is the
    -- effective weight (negative for penalties, positive for credits).
    select
        e.league_key,
        e.season_year           as settings_season,
        f.value:statId::integer as espn_stat_id,
        case
            when f.value:isReverseItem::boolean
                then -1.0 * f.value:points::double
            else f.value:points::double
        end                     as points_per_unit
    from latest_extraction e,
        lateral flatten(input => e.raw_json) f
)

select
    fl.league_key,
    fl.settings_season,
    fl.espn_stat_id,
    sc.stat_name,
    sc.stat_category,
    fl.points_per_unit
from flattened fl
inner join {{ ref('stat_classification') }} sc
    on fl.espn_stat_id = sc.espn_stat_id
