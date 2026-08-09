-- assert_slot_classification_covers_observed_slots.sql
--
-- MLB-222 F-1's alarm, and the reason the value-time fallback is allowed to
-- be a quiet 'hitting'.
--
-- stg_box_scores resolves lineup_slot_category by joining the
-- slot_classification seed and falling back to 'hitting'. That fallback is
-- a floor rather than a guess ONLY while this test holds: if a slot the
-- league actually uses has no seed row, the build fails here and names it,
-- instead of the slot silently becoming active hitting production.
--
-- Why the alarm is here and not in the value. lineup_slot_category is used
-- as a STAT FILTER by int_player_daily (c.stat_category =
-- d.lineup_slot_category), so a NULL or 'unclassified' category matches
-- nothing and DELETES the player's stats -- which is the pitcher-side
-- failure F-1 exists to kill. Making the bad case louder at value time
-- makes it quieter in the output. Build time is where it belongs.
--
-- Remediation for a failure: add one row to
-- dbt_league/seeds/slot_classification.csv, then
-- `dbt seed --full-refresh -s slot_classification`.
--
-- Two sources are checked:
--   1. Slots OBSERVED in player-day data, per platform. This is what
--      catches a stranger's league using a slot neither pioneer league has.
--   2. Slots the roster SETTINGS dictionary knows (dim_roster_slot_counts),
--      which catches a settings slot nobody has been deployed into yet --
--      before it shows up mid-season.
--
-- CBS ACT/EST/U carry a NULL slot_category on purpose (they are MLB-226's
-- call, and int_cbs__player_daily classifies CBS separately today). They
-- have seed ROWS, so they pass coverage without claiming a category.

with observed as (
    select distinct
        case when league_key like 'cbs%' then 'cbs' else 'espn' end as platform,
        lineup_slot
    from {{ ref('fct_player_daily_performance') }}
    where lineup_slot is not null

    union

    select distinct
        'espn' as platform,
        lineup_slot
    from {{ ref('dim_roster_slot_counts') }}
    where lineup_slot is not null
),

seeded as (
    select platform, lineup_slot
    from {{ ref('slot_classification') }}
)

select
    o.platform,
    o.lineup_slot,
    'lineup_slot ' || coalesce(o.lineup_slot, '<null>') || ' (platform '
        || o.platform || ') has no row in seeds/slot_classification.csv, so '
        || 'stg_box_scores would silently classify it as hitting. Add the '
        || 'row, then: dbt seed --full-refresh -s slot_classification'
        as failure_reason
from observed o
left join seeded s
    on s.platform = o.platform
    and s.lineup_slot = o.lineup_slot
where s.lineup_slot is null
