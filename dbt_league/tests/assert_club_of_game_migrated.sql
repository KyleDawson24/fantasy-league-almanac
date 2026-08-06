-- Singular test: the club-of-game flip needs migrated RAW, and an install
-- that has not run the backfill must FAIL rather than build green with a
-- silently null chart (MLB-200).
--
-- WHY THIS EXISTS. stg_box_scores reads `clubOfGame` and nothing else for
-- pro_team (MLB-159 Exit 1). Pre-flip RAW has no such key: it carries
-- `proTeam` only, and the key is written by extract.py's
-- `--backfill-club-of-game` pass. Nothing in the chain noticed the
-- difference. An existing install upgrading across the flip would build
-- every model green while its historical affinity chart quietly went
-- null/Unattributed, and the only warning lived deep in Known Data
-- Issues. This is not a speculative edge case -- the flip-prep handoff
-- witnessed exactly this on a stale local warehouse with zero clubOfGame
-- keys, and the ceremony refreshed RAW by hand before parity. A known
-- local prerequisite never became a product-wide upgrade gate.
--
-- WHAT COUNTS AS EVIDENCE. Only a row that PRODUCED can be expected to
-- carry a club: null means "did not appear that day", which is the honest
-- answer and not a gap. So the population is producing rows
-- (games_played > 0), and within that, the ROSTERED ones.
--
-- THE EXEMPTED RESIDUAL is the FA slot. Those are MLB-193's rows -- free
-- agents ESPN no longer serves, so no split survives to name a club. They
-- are deliberately left NULL rather than guessed, carry zero chart
-- weight, and are a measured population rather than a category anyone
-- hopes is small: at the time of writing, every single missing-club
-- producing row in the warehouse is one of them (506 rows, all
-- espn-main 2026, all lineup_slot = 'FA'; rostered producing rows are
-- 66,778 of 66,778 attributed). Exempting the slot rather than a row
-- count means the gate keeps working as that number moves.
--
-- TELLING "NOT MIGRATED" APART FROM "GENUINELY MISSING". The ticket asked
-- for a RAW provenance marker. The period-level signature is the same
-- discriminator without rewriting settled RAW -- which MLB-188 exists to
-- forbid, and which a marker would require for every already-loaded row.
-- Un-migrated RAW has no clubOfGame key ANYWHERE, so its periods come
-- back with zero attributed rows; a genuine gap is one player-day inside
-- a period that is otherwise attributed. The failure_reason column says
-- which one you are looking at, because the two have different fixes.
--
-- Returns one row per unattributed rostered producing player-day;
-- zero rows = pass.

with producing as (

    select
        league_key,
        season_year,
        scoring_period,
        player_id,
        lineup_slot,
        pro_team
    from {{ ref('stg_box_scores') }}
    where games_played > 0

),

rostered as (

    -- FA is the exempted residual (MLB-193), not an oversight.
    select * from producing where lineup_slot <> 'FA'

),

period_state as (

    select
        league_key,
        season_year,
        scoring_period,
        count(pro_team) as attributed_rows
    from rostered
    group by 1, 2, 3

)

select
    r.league_key,
    r.season_year,
    r.scoring_period,
    r.player_id,
    r.lineup_slot,
    case
        when s.attributed_rows = 0
            then 'RAW NOT MIGRATED for this period -- run: python extract/extract.py --backfill-club-of-game --all --year <season>'
        else 'club evidence missing for a producing rostered player-day'
    end as failure_reason
from rostered r
join period_state s
    on  r.league_key     = s.league_key
    and r.season_year    = s.season_year
    and r.scoring_period = s.scoring_period
where r.pro_team is null
