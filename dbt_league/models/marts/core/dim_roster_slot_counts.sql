-- dim_roster_slot_counts.sql
-- Consumer-facing dimension for roster slot settings. Reshapes ESPN's
-- raw rosterSettings payload into one row per configured lineup slot,
-- including starter counts and per-position maximums where ESPN exposes
-- them.
--
-- Two ESPN dictionaries feed this model:
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

with latest_extraction as (
    select
        season_year,
        raw_json
    from {{ source('raw', 'roster_settings') }}
    qualify row_number() over (
        partition by season_year
        order by extracted_at desc
    ) = 1
),

slot_map as (
    select
        column1::integer as lineup_slot_id,
        column2::varchar as lineup_slot,
        column3::integer as sort_order,
        column4::integer as position_limit_id
    from values
        (0,  'C',     10,  2),
        (1,  '1B',    20,  3),
        (2,  '2B',    30,  4),
        (3,  '3B',    40,  5),
        (4,  'SS',    50,  6),
        (6,  '2B/SS', 55,  null),
        (7,  '1B/3B', 56,  null),
        (19, 'IF',    60,  null),
        (8,  'LF',    70,  7),
        (9,  'CF',    80,  8),
        (10, 'RF',    90,  9),
        (5,  'OF',    100, null),
        (11, 'DH',    110, 10),
        (12, 'UTIL',  120, null),
        (13, 'P',     130, null),
        (14, 'SP',    140, 1),
        (15, 'RP',    150, 11),
        (16, 'BE',    900, null),
        (17, 'IL',    910, null)
),

lineup_slot_counts as (
    select
        e.season_year,
        f.key::integer as lineup_slot_id,
        f.value::integer as starter_count
    from latest_extraction e,
        lateral flatten(input => e.raw_json:lineupSlotCounts) f
),

position_limits as (
    select
        e.season_year,
        f.key::integer as position_limit_id,
        f.value::integer as raw_position_limit
    from latest_extraction e,
        lateral flatten(input => e.raw_json:positionLimits) f
)

select
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
    on lsc.season_year = pl.season_year
    and sm.position_limit_id = pl.position_limit_id
