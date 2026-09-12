-- Singular test: on the acquisition mart, opening_<lens>_pts must equal the
-- plain two-column sum keeper_<lens>_pts + draft_<lens>_pts, both lenses,
-- exactly (MLB-263, ledger S-01).
--
-- OPENING is the season-points Advanced Standings' vocabulary for "what the
-- team started the season holding"; ESPN reports the pair as keeper and
-- draft and the H2H book shows them split. The collapse used to live in a
-- Python bridge (almanac_render.with_standard_acquisition_channels) that
-- summed the two floats with no rounding, and the mart column was built to
-- reproduce that sum bit for bit -- measured 28/28 when the column landed.
-- Now that the readers take opening_* from the mart and the bridge is gone,
-- this test is the durable guard on the seam: behavioural, not prose.
--
-- ZERO TOLERANCE, DELIBERATELY. The mart's other totals round once at this
-- grain; opening_* must not, or the number the presenter prints shifts in
-- its last decimal against the two components beside it. `is distinct from`
-- is the exact comparison, so a NULL on either side is also a violation --
-- the mart coalesces every operand to 0, and a NULL here means it stopped.
--
-- Returns one row per violating (league, season, team, lens); zero rows =
-- pass.

select league_key, season_year, team_id, 'active' as lens,
       opening_active_pts                       as got,
       keeper_active_pts + draft_active_pts     as expected
from {{ ref('mart_team_acquisition_channels') }}
where opening_active_pts is distinct from keeper_active_pts + draft_active_pts

union all

select league_key, season_year, team_id, 'rostered' as lens,
       opening_rostered_pts                       as got,
       keeper_rostered_pts + draft_rostered_pts   as expected
from {{ ref('mart_team_acquisition_channels') }}
where opening_rostered_pts is distinct from keeper_rostered_pts + draft_rostered_pts
