-- dim_lineup_slot.sql
-- The PLATFORM-GENERAL lineup-slot vocabulary (MLB-263, opening MLB-6's
-- move #1): every slot each platform knows, projected onto the identity it
-- shares with the other platform's spelling of the same thing.
--
-- ==========================================================================
-- GRAIN: one row per (platform, lineup_slot).
-- ==========================================================================
--
-- WHY IT EXISTS NOW. slot_classification.canonical_key has been carried
-- deliberately unread since MLB-222 F-1, and its own seed documentation says
-- why: "NOTHING READS THIS YET ... There is no canonical_slots dimension to
-- point at -- building one is MLB-6's call". This is that dimension. The
-- eligibility surface is the first consumer that genuinely needs it: it
-- counts a team's roster by POSITION across BOTH platforms in one table, and
-- ESPN 'UTIL' / CBS 'U' cannot be two columns in a shared grid.
--
-- WHAT IT ADDS over reading the seed directly is one derived column,
-- is_position_slot, and that column is the whole point. Consumers keep
-- reaching for the same question -- "is this slot a real fielding position,
-- or roster machinery?" -- and keep answering it with a literal list. The
-- lists disagree: almanac_logic carried its own _PITCHING_SLOTS frozenset,
-- fct_player_position_pts spells the exclusion `not in ('BE', 'IL')`, and
-- espn_points_data keeps a _NON_POSITION_SLOTS tuple. Every one of them is a
-- hardcoded slot list of the exact kind MLB-222 F-1 removed from the
-- category lookup, and every one of them silently misfiles a slot nobody
-- thought of.
--
-- HOW is_position_slot IS DECIDED -- from the seed's own columns, so a new
-- slot is a CSV edit and never a model edit:
--
--   is_starting_slot   false for BE / IL / FA / RS, NULL for CBS EST. A slot
--                      a player is not deployed in is not a position.
--   slot_category      NULL for CBS ACT, and that NULL is load-bearing (the
--                      seed says so): ACT is a generic active slot carrying
--                      both hitters and pitchers, so it is not a position
--                      either -- it is the ABSENCE of one. Requiring a real
--                      category excludes it without naming it.
--   canonical_key      the single exclusion stated as an identity rather
--                      than a label: 'utility' is a flex, not a position,
--                      and writing it canonically excludes ESPN UTIL and
--                      CBS U in one clause instead of two.
--
-- WHAT IT DELIBERATELY DOES NOT DO is scope itself to one league. This is a
-- VOCABULARY, not a roster setting: it says what a slot means on a platform,
-- not which slots a particular league configured. That second question is
-- dim_roster_slot_counts' job (it reads rosterSettings per league-season),
-- and consumers that need "the positions THIS league fields" join the two.
-- Keeping them separate is what stops a league's settings from editing the
-- meaning of a slot.
--
-- FLEX SLOTS RIDE AS POSITIONS, and they are NOT a redundant composite of
-- the atomic positions they appear to sum (Kyle's ruling, 2026-08-31 --
-- recorded here because the tempting simplification is wrong and will be
-- proposed again).
--
-- The reason is the eligibility RULE, not the label. Eligibility is earned
-- per position by games played -- 20 in the prior season or 10 in this one
-- -- and the flex slot earns SEPARATELY on the combined total. A player
-- with 10 games in left, 10 in center and 10 in right qualifies at OF and
-- at NONE of LF, CF or RF. Dropping the flex column would delete a
-- deployment the manager genuinely has, and reconstructing it from the
-- atomic columns is impossible: they are all zero in exactly the case the
-- flex column exists to describe.
--
-- So 2B/SS, 1B/3B, IF and OF are independent information, they are how
-- ESPN itself expresses eligibility in eligibleSlots, and they stay.
--
-- Materialization: view. Nine bytes of seed reshaped; it changes only when
-- the seed does.
{{ config(materialized='view') }}

select
    platform::varchar                       as platform,
    lineup_slot::varchar                    as lineup_slot,
    lineup_slot_id::integer                 as lineup_slot_id,
    canonical_key::varchar                  as canonical_slot_key,
    sort_order::integer                     as sort_order,
    position_limit_id::integer              as position_limit_id,
    slot_category::varchar                  as slot_category,
    coalesce(is_starting_slot, false)       as is_starting_slot,

    -- The derived contract. See the header for why each clause is here and
    -- why none of them names a slot label.
    -- coalesce()d to false around the WHOLE expression, not just the
    -- is_starting_slot term, and a not_null test pins it. CBS ACT is why:
    -- it is a starting slot with a NULL slot_category, so the middle
    -- clause is NULL rather than false and three-valued logic makes
    -- `true and NULL` -> NULL. The row then answered "unknown" to a
    -- boolean question, and a consumer filtering `where is_position_slot`
    -- would drop it while one filtering `where not is_position_slot`
    -- would drop it too -- the slot disappears from both sides of a
    -- partition. Not hypothetical: the test failed on exactly this row.
    coalesce(
        coalesce(is_starting_slot, false)
        and slot_category in ('hitting', 'pitching')
        and coalesce(canonical_key, '') <> 'utility',
        false
    )                                       as is_position_slot,

    -- HOW THE SLOT IS LABELLED TO A READER, which is not always how the
    -- platform spells it (Kyle, 2026-08-31). ESPN writes the two
    -- two-position flexes as '2B/SS' and '1B/3B'; the league calls them MI
    -- and CI, and so does every other fantasy surface.
    --
    -- A SEPARATE COLUMN rather than a renamed lineup_slot, deliberately.
    -- lineup_slot is a JOIN KEY -- box-score rows, dim_roster_slot_counts
    -- and the eligibility mart all match on the platform's own string --
    -- so renaming it in the seed would not relabel a column, it would
    -- silently stop matching and drop the slot out of every consumer.
    --
    -- Keyed off canonical_slot_key, so a platform that spells the same
    -- flex differently gets the same reader-facing label for free.
    case coalesce(canonical_key, '')
         when 'middle_infield' then 'MI'
         when 'corner_infield' then 'CI'
         else lineup_slot
    end::varchar                            as display_slot,

    notes::varchar                          as notes
from {{ ref('slot_classification') }}
