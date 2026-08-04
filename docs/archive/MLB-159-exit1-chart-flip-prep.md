# MLB-159 Exit 1 — the chart flip, prepared: overnight session report

**Branch:** `exit1/chart-flip` (cut from `exit1/game-level` @ 6dd7cd7)
**Run:** 2026-08-04, overnight · **Scope:** branch-only. Nothing pushed, no
Linear write, no golden re-anchored, no shipped-sheet write. RAW read-only.

Kyle ruled two questions at kickoff (both as recommended):

1. **Label semantics = latest-in-scope.** `max_by(pro_team, <recency>)` at
   every collapse grain, matching the display convention
   `fct_player_season_performance` already documents.
2. **Diff radius = fix all ESPN-activated MLB-168 sites**, and attribute
   every diffed file in the gate table by cause.

---

## 1. MLB-168 sweep — the site table

Done FIRST because the flip activates it: the alphabetical pick is harmless
only while MLB-159's defect freezes `pro_team` to one value per player.

| site | grain | rule before | verdict |
|---|---|---|---|
| `fct_player_position_pts.sql:152` | matchup period | `max(pro_team)` | **FIXED** — `latest_by(pro_team, scoring_period)` |
| `almanac_data.py` `get_optimal_team_candidates` | career / filtered scope | `MAX(pro_team)` | **FIXED** — latest by `season_year*1000 + period` |
| `almanac_data.py` `get_optimal_season_candidates` | season | `MAX(pro_team)` | **FIXED** — latest by period |
| `generate_summary.py` `player_meta` | latest row per player | `ROW_NUMBER` row-pick | **FIXED** — window form (see below) |
| `generate_season_report.py` `player_meta` | latest row per player | `ROW_NUMBER` row-pick | **FIXED** — window form |
| `int_cbs__player_daily.sql:120` | already daily (`roster_date`) | `max(pro_team)` | **no change** — no-op de-dup, measured 0 groups carry two clubs |
| `cbs_almanac_sheets.py:2291` | player_key (CBS) | `MAX_BY(pro_team, game_date)` | **no change** — already latest-by-date; adding the guard changes 0 of 3,095 players |
| `cbs_almanac_sheets.py:2563` | player_key (CBS) | `COALESCE(MAX_BY(...), MAX(current_club))` | **no change** — already guarded |
| `almanac_data.py:1459` `_PRO_TEAM_MAP` | Trades tab | live ESPN API path | **not the warehouse column** — untouched |

**Deliberately NOT swept:** the ticket's "consider `MAX(player_name)` /
`MAX(display_name)` too" item. Those are outside the flip's blast radius and
changing them would move goldens for a second, unrelated cause — the exact
mixing MLB-168 itself warns against. Enumerated, left alone, flagged here.

### Two traps found by measuring rather than assuming

**(a) `game_date` is the wrong ordering key — it is NULL on 100% of ESPN
rows.** `int_player_daily` stamps `cast(null as date)` deliberately (ESPN's
day is a period index, not a calendar date); CBS carries the real date.
Ordering by it returns NULL for **all 34,738** ESPN groups. `scoring_period`
is the universal key: non-null on both books, chronological within a season
(ESPN 1..195, CBS the date itself as YYYYMMDD).

**(b) The null guard is load-bearing, and the naive spelling diverges across
engines.** Verified on both:

| case | Snowflake `max_by` | DuckDB `max_by` |
|---|---|---|
| latest row's label NULL | **NULL** | `'TB'` |
| guarded (null the ORDER key) | `'TB'` | `'TB'` |
| all rows NULL | NULL | NULL |
| tie on the sort key | same row | same row |

Post-flip `pro_team` is NULL on 52–55% of rostered player-days, so the
unguarded form would blank the club for anyone whose last day in scope was a
rest day. The guarded form is the one spelling both engines agree on — which
is why it lives in `macros/latest_by.sql` + `db.latest_by()` rather than in
`dialect.sql` (after the guard the engines do **not** disagree).

**Ties are not broken, deliberately.** Structurally unreachable on ESPN
(`clubOfGame` is one scalar per player-day entry) and measured absent today
(0 groups). Noted rather than built.

### The fix is provably inert pre-flip

Run against today's frozen data, at the fct grain:

| book | groups | change under the new rule | go NULL |
|---|---|---|---|
| espn-main | 34,738 | **0** | 0 |
| cbs-bsb | 19,142 | 1 | 0 |

ESPN moves nothing — exactly what MLB-168 predicts ("correct today, and only
by accident"). **So any golden movement after the flip is attributable to the
flip alone**, which is the cause-separation the ticket asks for.

---

## 2. The flip

`pro_team` now reads RAW's `clubOfGame` on all three ESPN unions (home
lineup, away lineup, free agents). CBS untouched. No new columns.

The operative attribution rule is the **producing-splits filter** — only a
split carrying a non-empty `stats` object is club evidence. Majority-by-
production stays upstream as the documented dormant fallback (MLB-188
supersession note); phantom stat-less splits are never club evidence.

**Coverage, measured on RAW (read-only):**

| season | bucket | entries | key absent | club NULL |
|---|---|---|---|---|
| 2025 | home / away / FA | 44,725 / 44,712 / 28,361 | 0 / 0 / 0 | 24,477 / 24,416 / 0 |
| 2026 | home / away / FA | 27,384 / 27,449 / 22,315 | 0 / 0 / 0 | 14,171 / 14,428 / 506 |

The key is present on **100%** of stored player entries. The NULLs are not a
gap — they are exactly the did-not-play population, a perfect partition with
zero cross-contamination:

| season | population | entries | club NULL |
|---|---|---|---|
| 2025 | played / has stats | 40,544 | **0** |
| 2025 | did not play / no stats | 48,893 | **48,893** |
| 2026 | played / has stats | 26,234 | **0** |
| 2026 | did not play / no stats | 28,599 | **28,599** |

That independently corroborates the handoff's "0 null clubs on active-slot
production rows" from a different direction.

**Kept rather than cleaned up:** the `pro_team = 'FA'` arm of the affinity
CASE is now unreachable (`clubOfGame` is one of 30 clubs or NULL, never
'FA'). It stays as the tripwire against an FA filter being silently
restored — the regression that once deleted 11.7% of 2025 from the chart.
`tests/test_almanac_sheets.py:1429` pins its text and still passes.

---

## 3. CBS symmetry (flip-spec ⑤b) — one rule, both books, already

**CBS's affinity chart is ALREADY game-accurate**, by a different mechanism.
`get_mlb_affinity` groups by `g.team_id` — the MLB club *of the game* —
joining the attribution fact to the gamelog on
`(league_key, cbs_player_id, stat_group, game_date, game_pk, game_index)`.

**It never collapses to one club per player-period**, so a genuine two-club
day contributes to BOTH clubs, each with its own weight. There is no
collapse to lose and nothing to align. The two books now state the same
rule — credit production to the club of the game — and differ only in
resolution: ESPN attributes at player-DAY grain (one `clubOfGame` scalar per
entry), CBS at player-GAME grain.

**Genuine two-club days DO exist here** — the Youngblood class is real in 26
seasons. 10 player-days in the MLB spine carry two clubs:

| date | mlbam | clubs | shape |
|---|---|---|---|
| 2008-04-28 | 115135 | White Sox \| Reds | genuine (2 game_pks) |
| 2009-05-05 | 460579 | Pirates \| Nationals | genuine |
| 2019-05-19 | 518617 | Royals \| Athletics | genuine |
| 2021-04-11 | 595879 | Cubs \| Mets | genuine |
| 2021-07-21 | 543339 | Padres \| Nationals | genuine (also hitting+pitching) |
| 2021-07-21 | 624585 | Braves \| Royals | genuine |
| 2021-07-21 | 545350 | Cubs \| Padres | genuine |
| 2021-07-21 | 594807 | Braves \| Marlins | genuine |
| 2024-06-26 | 694388 | Astros \| Blue Jays | genuine |
| 2024-06-26 | 643376 | Red Sox \| Blue Jays | **ARTIFACT — one game_pk** |

**5 of the 10 carry real CBS chart weight** and are credited to both clubs
correctly.

### Flagged, not fixed: one spine defect

`mlbam 643376` on 2024-06-26 appears under **both** Toronto (141) and Boston
(111) inside a single `game_pk` (746942) — listed as his own opponent, 20
stat rows each side. That is a data defect in `stg_mlb__player_game`, not a
two-club day.

**It is inert today**: measured `active_weight = 0`, so it carries no chart
weight on either book. Not fixed here — it is outside the flip's scope and
fixing it would move CBS goldens for an unrelated cause. Worth its own
ticket.

---

*(sections 4–7 appended as the session proceeds)*
