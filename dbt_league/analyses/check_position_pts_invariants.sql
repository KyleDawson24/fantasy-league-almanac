-- Sanity invariants for int_player_position_pts. Returns one row per
-- check; expects "ok" in result column for every row. Any row with
-- result != 'ok' means an invariant violated.
--
-- Checks (the original sum invariant was dropped along with the all_pts
-- column -- see model docstring; sum-of-rounded-vs-rounded-sum drift
-- made that column more misleading than useful):
--   1. Trout has multi-position rows (OF/CF/UTIL/DH) for 2026
--   2. Soto picked up DH+LF mid-season per the earlier eligibility
--      data check; both rows should exist with eligible_days >= 1
--   3. BE / IL don't leak through as position codes (LATERAL FLATTEN
--      filter is working)
--   4. FA rows present (team_id IS NULL appears at least sometimes)

with check_trout_or_no_match as (
    select
        case when count(*) > 0 then 'ok' else 'FAIL: no Trout rows found' end as result
    from {{ ref('int_player_position_pts') }}
    where display_name ilike '%Trout%'
      and season_year = 2026
      and position in ('OF', 'CF', 'UTIL', 'DH')
),

check_soto_position_pickup as (
    -- Soto picked up DH + LF eligibility on scoring_period 29 in 2026.
    -- His DH/LF rows for matchup_period containing day 29+ should
    -- exist and have eligible_days >= 1.
    select
        case when count(*) >= 2 then 'ok'
             else 'FAIL: expected Soto DH+LF eligibility post-pickup; got ' || count(*) || ' rows' end as result
    from {{ ref('int_player_position_pts') }}
    where display_name ilike '%Soto%'
      and season_year = 2026
      and position in ('DH', 'LF')
      and eligible_days > 0
),

check_no_be_or_il as (
    -- BE and IL should NOT appear as position codes
    select
        case when count(*) = 0 then 'ok'
             else 'FAIL: BE/IL leaked through as position; ' || count(*) || ' rows' end as result
    from {{ ref('int_player_position_pts') }}
    where position in ('BE', 'IL')
),

check_fa_team_null as (
    -- FA rows: team_id IS NULL but player_id IS NOT NULL.
    -- At least some FA rows should exist (free agency happens).
    select
        case when count(*) > 0 then 'ok'
             else 'WARN: no FA rows -- check upstream FA handling' end as result
    from {{ ref('int_player_position_pts') }}
    where team_id is null
)

select 'Trout multi-position rows' as check_name, result from check_trout_or_no_match
union all select 'Soto DH/LF post-pickup', result from check_soto_position_pickup
union all select 'no BE/IL in position',   result from check_no_be_or_il
union all select 'FA rows present',        result from check_fa_team_null
