# 2001–2002 roster backfill (Track B manual worklist)

**Why this exists.** Season-end roster capture only goes back to 2003, so for 2001
and 2002 the walk-back has no year-end anchor. It reconstructs day-grain rosters
from the transaction log — which works for any player who was *added, dropped,
traded, or reserved*. The gap is players who were **drafted and simply held all
season**: they never generated a transaction, so the log can't tell us whose team
they were on. Those are the stars (Bonds's 73, Sosa's 64, …). This file is that
list — the never-transacted producers — for you to assign a team.

Everyone in this list was never reserved either (a reserve move is a transaction),
so they were **active all season** → `active_status` defaults to `A`. Only change
it to `RS` if you know a player was stashed on the bench all year.

## How to fill it

File: `dbt_league/seeds/cbs_early_anchors_backfill.csv`

- Put the team **abbrev** (from the legend below) in `team_abbrev_FILL_ME`.
- If you did **not** roster a player that year, leave the cell blank or delete the
  row — some of these produced in MLB but weren't owned in the league.
- `tier = star` (>300 pts) are the record-relevant ones — do these first.
  `tier = tail` (100–300 pts) is optional completeness.
- Leave `cbs_player_id` and the other columns alone; that id is how the value
  gets fed back into the warehouse.

## 2001–2002 team legend (abbrev → what it was called then)

The abbrev is the stable franchise key; the *name* changed over the years, so match
on the era name you remember:

| abbrev | 2001 name              | 2002 name (if different) |
|--------|------------------------|--------------------------|
| FLV    | Pilgrims Pacific       |                          |
| TGUN   | Armonk Artillery       | *(no 2002 — folded/renamed)* |
| WOL    | Wizards of Lisbon   |                          |
| JUNK   | SaintsNSinners          |                          |
| NYN    | Bob's Blue Sox         | New York Nightowls     |
| KCM    | Kansas City Meteors   |                          |
| CYB    | Cybersaurs              |                          |
| EBB    | Hey, Ebbtide!           |                          |
| DOB    | Dugout Doughboys      |                          |
| HH     | Hardball Hackers       |                          |
| HAY    | Windmill Haymakers      |                          |
| NSFY   | No Stats For You        |                          |
| FULT   | Foster's Folly         |                          |
| BENT   | California Sun Gods     | Bent Spokes              |
| CSC    | Cedar Street Centaurs |                          |
| SED    | Filthy Lucre           |                          |

**Two loose ends I'll chase on the build side (not yours to solve):**
1. 2002 has no Armonk Artillery (TGUN) but shows an unmapped "Nightowls" bucket
   of ~193 moves alongside the mapped New York Nightowls — looks like one 2002
   team's moves split across two franchise ids. I'll resolve the mapping.
2. If a team you remember from 2001–2002 isn't in the legend, tell me the name and
   I'll trace its franchise id from the log.
