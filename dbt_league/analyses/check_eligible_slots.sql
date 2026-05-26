-- Sanity check that eligible_slots data is shaped how Approach 1 needs.
-- For a few well-known players, show their season's distinct eligibility
-- sets + the day they first achieved each. Confirms:
--   (1) it's a VARIANT array per row (LATERAL FLATTEN works)
--   (2) values match lineup_slot codes from dim_roster_slot_counts
--   (3) eligibility actually changes mid-season for the right players
--   (4) coverage looks reasonable (not mostly NULL)

with season_eligibility as (
    select
        d.season_year,
        d.player_id,
        d.player_name,
        d.scoring_period,
        slot.value::string as eligible_slot
    from {{ ref('fct_player_daily_performance') }} d,
    lateral flatten(input => d.eligible_slots) slot
    where d.season_year = 2026
),

first_day_per_slot as (
    select
        player_name,
        eligible_slot,
        min(scoring_period) as first_eligible_day
    from season_eligibility
    where player_name in (
        'Shohei Ohtani', 'Jacob deGrom', 'Mike Trout',
        'Juan Soto', 'Mookie Betts', 'Bryce Harper'
    )
    group by 1, 2
)

select * from first_day_per_slot
order by player_name, first_eligible_day, eligible_slot
