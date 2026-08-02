# Two-Way Player Split Bug — Handoff

_Single-bug handoff, 2026-06-01. Scope: fix the platform hitting/pitching
split for two-way players (the "classic Ohtani goof"). Nothing else._

## Symptom

The weekly recap's player superlatives mislabel two-way players. In the live
MP10 (Week 10) recap:

```
Top Hitter: Shohei Ohtani (WALK), 53.1 pts -- .480/.552/.760 -- 25 AB, 8 1B, 7 R
```

53.1 is his hit + pitch TOTAL (~25.6 hitting + ~27.46 pitching), not his
hitting. And the two-way **Top Scorer** line -- which *should* feature Ohtani
-- did not render at all. (He is, ironically, not detected as two-way.)

## Confirmed root cause

`dbt_league/models/intermediate/int_player_daily.sql` lines ~226-227 split
platform points **all-or-nothing by lineup slot**:

```sql
case when w.lineup_slot in ('SP','RP','P') then 0 else b.points end as platform_hitting_pts,
case when w.lineup_slot in ('SP','RP','P') then b.points else 0 end as platform_pitching_pts,
```

`b.points` is ESPN's whole-day total. Ohtani pitched on a day ESPN had him in
a hitting slot (DH/UTIL), so his pitching points got dumped into
`platform_hitting_pts`. The stored MP10 fact row
(`fct_weekly_player_active_performance`):

```
platform_hitting_pts:    53.1   (= full day total)
platform_pitching_pts:   0.0
calculated_hitting_pts:  31.0
calculated_pitching_pts: 0.0
```

## The recap logic is already correct -- do NOT rewrite it

`output/generate_summary.py`:
- `find_top_hitter` selects by `platform_hitting_pts`; `format_hitter_line`
  displays it. `find_top_pitcher` / `find_top_scorer` are symmetric.
- The two-way **Top Scorer** gate is `platform_hitting_pts > 0 AND
  platform_pitching_pts > 0` (~lines 715-731).

So with the DATA fixed, the existing logic produces the maintainer's spec
below. The only recap-side work should be confirming that.

## Desired behavior (maintainer spec)

- **Line A -- Top Scorer:** `Name (ABBR), ##.# Pts -- [top-5 point-producer
  stat line]`. **Renders only when the top scorer had nonzero hitting AND
  nonzero pitching points.**
- **Line B -- Top Hitter:** `Name (ABBR), ##.# Pts -- [slash line], [top-3
  point-producer stat line]`. Selected by hitting points.
- **Line C -- Top Pitcher:** `Name (ABBR), ##.# Pts -- [slash line], [top-3
  point-producer stat line]`. Selected by pitching points.

(Format already matches today -- pitcher "slash" = W-L-Sv / ERA / WHIP.)

## The hard part -- a two-way modeling knot

The platform and calculated lenses currently DISAGREE on whether a two-way
player's pitching counts from a hitting slot:

- `platform` total (53.1) INCLUDES his pitching -- ESPN credits a two-way
  player's pitching even from a UTIL/DH slot.
- `calculated_pitching_pts = 0` -- the slot-validity filter
  (`stat_category = lineup_slot_category`, var `strict_slot_validity`) zeroes
  pitching stats in a hitting slot.

So you canNOT just sum the per-stat `*_pts` columns to split -- Ohtani's
pitching `*_pts` are already filtered to 0. A real fix must:
1. Attribute the platform total to hitting vs pitching by **stat
   contribution**, not slot -- likely needing the UN-filtered hitting vs
   pitching stat points; and
2. **Resolve the philosophical question the model answers two different
   ways:** does a two-way player's pitching count from a hitting slot? ESPN
   says yes (platform 53.1); the calculated lens says no (calc 31.0). Pick
   one and make platform + calculated consistent.

Also note: `calculated_hitting_pts = 31.0` vs the maintainer's eyeballed
~25.6 hitting is itself a clue -- platform and calculated *should* agree for
the current season. Understand that gap as part of the same knot.

## Where to investigate

- `dbt_league/models/intermediate/int_player_daily.sql` -- the split (226-227),
  the slot-validity filter, `lineup_slot_category`.
- `dbt_league/models/staging/stg_box_scores.sql` -- `lineup_slot_category`
  derivation. The slot list MUST match int_player_daily's (comment warns of
  silent misclassification on divergence).
- `Phase 4.0 Documentation.md` (repo root) -- the original two-way /
  slot-validity / `strict_slot_validity` decision and rationale.
- `output/generate_summary.py` -- `find_top_scorer/hitter/pitcher`,
  `format_top_scorer_line / format_hitter_line / format_pitcher_line`, and
  the two-way gate. Confirm no recap change is needed beyond the data fix.
- Confirm the mechanism: query Ohtani's PER-DAY rows
  (`int_player_daily` or `fct_player_daily_performance`) for MP10 to see his
  daily slot + points and the DH-day-with-pitching that triggers the leak.

## Acceptance test

- Ohtani MP10: `platform_hitting_pts ≈ 25.6`, `platform_pitching_pts ≈ 27.46`
  (sum ≈ 53.1). Verify against ESPN's actual hitting/pitching breakdown.
- Recap MP10: **Top Scorer = Ohtani (~53.1, two-way line renders)**; **Top
  Hitter = the actual hitting leader (NOT Ohtani)**; Top Pitcher = Noah
  Cameron (CAL, 45.1).
- A normal hitter and a normal pitcher are UNCHANGED (single-role split still
  lands entirely in one bucket).
- Team-level `platform_hitting_pts` / `platform_pitching_pts` still sum
  sensibly.

## Constraints

- Touches the facts -> needs a scoped `--full-refresh` of the player/team
  facts + downstream marts, then regenerate the byte-diff + records goldens
  (any week with a two-way player shifts) and diff-review them.
- Don't break single-role players (the common case).
- Keep the live Snowflake/ESPN weekly path working.

## Gathering context fast (warehouse is reachable)

Key-pair auth works from the worktree. A reusable probe:

```python
# temp snow_q.py
import sys
sys.path.insert(0, r"<worktree>/output")
import db; db.init()
from db import query_snowflake
for row in query_snowflake(sys.argv[1]):
    print(row)
```

Run it with the venv python (`.venv/Scripts/python.exe`) and a SQL string to
inspect Ohtani's daily + weekly rows against the live marts.

---

## Kickoff prompt (paste into the bug-fix session)

```
Single-bug session: fix the two-way player hitting/pitching split (the
"classic Ohtani goof"). Read "Two-Way Split Bug Handoff.md" in the repo root
-- it has the symptom, the confirmed root cause (int_player_daily.sql:226-227
splits platform points all-or-nothing by lineup slot, so a two-way player's
whole-day total lands in one bucket), the data evidence, the desired recap
behavior (Top Scorer / Top Hitter / Top Pitcher, with Top Scorer rendering
only when the leader had nonzero hitting AND pitching), the deeper knot
(platform credits a two-way player's pitching from a hitting slot, calculated
zeroes it -- they must be made consistent), the investigation map, and the
acceptance test (Ohtani MP10: platform_hitting ~25.6 / platform_pitching
~27.46; recap shows Top Scorer = Ohtani two-way, Top Hitter != Ohtani).
The recap LOGIC is already correct -- the fix is the upstream data split.
Don't break single-role players; needs a scoped --full-refresh + golden regen.
Start by confirming the mechanism in Ohtani's per-day rows, then decide the
hitting-vs-pitching attribution + resolve the platform/calculated slot-validity
disagreement.
```
