-- mart_cbs_draft_zip_fill.sql
-- Fill-rate accounting for the seasons assembled by MARRYING two pages
-- rather than reading one (MLB-90). One row per zip season.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year), zip seasons only.
-- ==========================================================================
--
-- Why this exists as its own model rather than being dropped:
--
-- A zip season is the one case where picks are INFERRED. 2024 recorded an
-- order skeleton with no players and per-team player lists with no order,
-- and int_cbs__draft_picks marries them on (team, k). That marriage is an
-- inner join, which silently discards both kinds of leftover -- skeleton
-- slots past the end of a team's list (passed late picks) and any listed
-- player that never found a slot.
--
-- Silently is the problem. Without these counts, a re-parse or re-capture
-- that changed how many slots found players would simply render a
-- different number of 2024 picks, with nothing to say so. These two
-- numbers are the season's fill-rate check, and the retired Python
-- assembly reported them for exactly that reason (unfilled_slots=13,
-- unslotted_players=0 at the time of the port).
--
-- Restricted to zip seasons on purpose: every other season IS a record
-- rather than an inference, so "slots that found no player" is not a
-- question its pages can even pose. Emitting 0s for them would imply a
-- check that had been performed.

{{ config(materialized='view') }}

with plan as (

    select * from {{ ref('draft_assembly_plan') }}
    where part_role in ('zip_skeleton', 'zip_players')

),

staged as (

    select * from {{ ref('stg_cbs__draft') }}

),

-- Every printed slot. Playerless BY NATURE -- this page is the order, not
-- the players -- so pick_no presence is what makes a row a slot.
skeleton as (

    select
        p.league_key,
        p.season_year,
        count(*) as skeleton_slots
    from plan p
    join staged d
      on  d.league_key = p.league_key
      and d.draft_key  = p.draft_key
      and d.view       = p.view
    where p.part_role = 'zip_skeleton'
      and d.pick_no is not null
    group by p.league_key, p.season_year

),

players as (

    select
        p.league_key,
        p.season_year,
        count(*) as listed_players
    from plan p
    join staged d
      on  d.league_key = p.league_key
      and d.draft_key  = p.draft_key
      and d.view       = p.view
    where p.part_role = 'zip_players'
      and not d.is_playerless
    group by p.league_key, p.season_year

),

matched as (

    select
        league_key,
        season_year,
        count(*) as picks
    from {{ ref('int_cbs__draft_picks') }}
    where order_tier = 'zip'
    group by league_key, season_year

)

select
    s.league_key,
    s.season_year,
    s.skeleton_slots,
    pl.listed_players,
    m.picks,

    -- Slots a team had no player left for: passed late picks.
    greatest(s.skeleton_slots - m.picks, 0) as unfilled_slots,

    -- Listed players that never found a slot. Should be 0 -- a non-zero
    -- here means the skeleton is smaller than the lists it is marrying,
    -- which would mean the pairing is losing real picks.
    greatest(pl.listed_players - m.picks, 0) as unslotted_players
from skeleton s
join players pl
  on  pl.league_key  = s.league_key
  and pl.season_year = s.season_year
join matched m
  on  m.league_key  = s.league_key
  and m.season_year = s.season_year
