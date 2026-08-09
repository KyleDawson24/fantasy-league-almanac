-- dim_roster_slot_counts.sql
-- Consumer-facing dimension for roster slot settings. Reshapes ESPN's
-- raw rosterSettings payload into one row per configured lineup slot,
-- including starter counts and per-position maximums where ESPN exposes
-- them.
--
-- Two ESPN dictionaries feed this model, both via the long-form
-- stg_roster_settings reshape:
--   - lineupSlotCounts: lineup slot IDs used by box score rows
--     (0=C, 14=SP, 16=BE, etc.)
--   - positionLimits: default MLB position IDs used for maximum roster
--     constraints (1=SP, 2=C, 11=RP, etc.)
--
-- Flex/roster-management slots such as IF, OF, UTIL, P, BE, and IL do
-- not have direct maximums in positionLimits, so they surface as N/A.
-- Raw -1 position limits mean "No Limit" in ESPN's UI.
--
-- Materialization: view. This is tiny and changes only when settings are
-- re-extracted.

{{ config(materialized='view') }}

with slot_map as (
    -- The ESPN slot dictionary, lifted out of this model and into the
    -- slot_classification seed (MLB-222 F-1) -- the move MLB-6 already
    -- calls for: "the slot_map VALUES block in dim_roster_slot_counts
    -- becomes per-platform mapping data rather than inline constants."
    --
    -- Roster SETTINGS supply the ids and the starter counts; the labels
    -- and what they mean are ours, so they are data rather than inline
    -- constants. Nothing here is derived from settings.
    --
    -- LIFT AND SHIFT ONLY: the same nineteen rows, the same output. The
    -- per-platform generalization stays with MLB-6. The null-id filter
    -- excludes the seed's synthetic FA row, which is an extract label
    -- rather than a roster slot and was never in this dictionary.
    select
        lineup_slot_id::integer    as lineup_slot_id,
        lineup_slot::varchar       as lineup_slot,
        sort_order::integer        as sort_order,
        position_limit_id::integer as position_limit_id
    from {{ ref('slot_classification') }}
    where platform = 'espn'
      and lineup_slot_id is not null
),

lineup_slot_counts as (
    select
        league_key,
        season_year,
        espn_id       as lineup_slot_id,
        setting_value as starter_count
    from {{ ref('stg_roster_settings') }}
    where setting_type = 'lineup_slot_count'
),

position_limits as (
    select
        league_key,
        season_year,
        espn_id       as position_limit_id,
        setting_value as raw_position_limit
    from {{ ref('stg_roster_settings') }}
    where setting_type = 'position_limit'
)

select
    lsc.league_key,
    lsc.season_year,
    lsc.lineup_slot_id,
    coalesce(sm.lineup_slot, 'SLOT_' || lsc.lineup_slot_id::varchar) as lineup_slot,
    lsc.starter_count,
    sm.position_limit_id,
    pl.raw_position_limit,
    case
        when pl.raw_position_limit is null then null
        when pl.raw_position_limit = -1 then null
        else pl.raw_position_limit
    end as maximum_count,
    case
        when pl.raw_position_limit is null then 'not_applicable'
        when pl.raw_position_limit = -1 then 'no_limit'
        else 'limited'
    end as maximum_type,
    case
        when pl.raw_position_limit is null then 'N/A'
        when pl.raw_position_limit = -1 then 'No Limit'
        else pl.raw_position_limit::varchar
    end as maximum_display,
    lsc.starter_count > 0
        and coalesce(sm.lineup_slot, '') not in ('BE', 'IL') as is_active_lineup_slot,
    coalesce(sm.sort_order, 999 + lsc.lineup_slot_id) as sort_order
from lineup_slot_counts lsc
left join slot_map sm
    on lsc.lineup_slot_id = sm.lineup_slot_id
left join position_limits pl
    on lsc.league_key = pl.league_key
    and lsc.season_year = pl.season_year
    and sm.position_limit_id = pl.position_limit_id
