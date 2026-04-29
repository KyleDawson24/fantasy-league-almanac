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
-- Grain: one row per stat_name (no season_year dimension - it's a
-- single-season reference table representing "the current rules").

with latest_season as (
    select max(season_year) as season_year
    from {{ source('raw', 'scoring_settings') }}
),

latest_extraction as (
    select
        season_year,
        raw_json
    from {{ source('raw', 'scoring_settings') }}
    where season_year = (select season_year from latest_season)
    qualify row_number() over (
        partition by season_year
        order by extracted_at desc
    ) = 1
),

flattened as (
    -- ESPN stores penalty stats (errors, earned runs allowed, walks allowed, etc.)
    -- with positive `points` magnitude AND isReverseItem=true. The reverse flag
    -- flips the sign at scoring time. We apply it here so points_per_unit is the
    -- effective weight (negative for penalties, positive for credits).
    select
        e.season_year           as settings_season,
        f.value:statId::integer as espn_stat_id,
        case
            when f.value:isReverseItem::boolean
                then -1.0 * f.value:points::float
            else f.value:points::float
        end                     as points_per_unit
    from latest_extraction e,
        lateral flatten(input => e.raw_json) f
)

select
    fl.settings_season,
    fl.espn_stat_id,
    sc.stat_name,
    sc.stat_category,
    fl.points_per_unit
from flattened fl
inner join {{ ref('stat_classification') }} sc
    on fl.espn_stat_id = sc.espn_stat_id
