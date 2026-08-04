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

## 4. Consumer enumeration — every reader of `pro_team`

`dbt build --target dev` (full chain, 77 models): **PASS=635, WARN=0,
ERROR=0, SKIP=0**, 543 data tests green, 3m57s.

| consumer | what it does with the club | behaviour under game-grain variation |
|---|---|---|
| `get_team_affinity_weights` (the chart) | buckets NULL/'FA' → Unattributed | **the target.** Unattributed → 0.0 in all three scopes |
| `almanac_logic` affinity band (MLB-190) | builds club set from the data; sentinel pinned last | **render-capable, correctly empty.** `AFFINITY_UNATTRIBUTED` simply never enters the club set, so no row is emitted — code path intact |
| `fct_player_position_pts` | collapses day → matchup period | FIXED (§1) |
| `get_optimal_team_candidates` / `..._season_candidates` | collapse period → career / season | FIXED (§1) |
| `generate_summary` / `generate_season_report` `player_meta` | row-pick, then read the club off that row | FIXED (§1, window form) |
| `almanac_render` (6 sites) | `row.get('pro_team') or ''` | NULL-safe by construction |
| `mart_daily_roster_snapshot` | passthrough | **no aggregation at all** — safe |
| `fct_player_season_performance` | does not carry the column | no exposure |
| `almanac_logic:2293` | reuses the Team CELL for overflow text | assigns, never reads — unaffected |
| `almanac_logic:2372` | Team column doubles as YEAR | assigns, never reads — see the note below |
| `cbs_almanac_sheets:2541` | `WHERE pro_team IS NOT NULL` | the only filter on the column anywhere; CBS-only |
| `almanac_data:1459` | `_PRO_TEAM_MAP` on the Trades tab | live-API path, not the warehouse column |

Nothing anywhere JOINs on `pro_team`. The one filter is CBS-only.

**The NULL population is provably unreachable from any team surface.** All
**647** null-label ESPN rows in `fct_player_position_pts` carry
`team_id IS NULL` — every one is a free-agent row, and **zero rostered rows
carry a null label**. They hold inactive points only (max active pts = 0),
so the `HAVING points > 0` on both optimal-team queries excludes them
regardless. This is exactly MLB-193's corner (506 player-days, 60 players,
2,186 involvement units, **0.0 chart weight**), and it stays NULL by ruling.

**Flagged, not changed:** the comment at `almanac_logic:2372` justifies
showing YEAR instead of Team in Best Individual Seasons with "pro_team is
only season-accurate on the CBS side". That rationale is now stale for ESPN
(game-accurate post-flip) but still true for CBS, and the display choice was
a product call. Left as-is; worth a sentence from you on whether the ESPN
book should now show the club there.

---

## 5. Doc drafts — for your voice-pass

### (a) The sheet explainer's replacement sentence

The old closing clause is dead text post-flip ("ESPN's player records carry
only a CURRENT club, so 2025 cannot place anyone who has changed clubs
since"). Per the MLB-188 ruling it is rewritten, not deleted, and rewritten
forward-true. **Applied to the code** so tonight's dev renders are coherent
rather than showing a false sentence — but it is a draft:

> Unattributed is involvement whose MLB club is unknown -- not free-agent
> time. Every club here is the club of the game the production came from,
> so this band stays empty while every game can be placed; a visible band
> means those seasons were reconstructed too late to place some of them.

Two alternates if you want it shorter or more inviting:

> ...not free-agent time. Clubs are the club of the game, so this band is
> empty here; if your league shows one, those seasons were backfilled after
> the fact.

> ...not free-agent time. Every club is the club of the game that produced
> the line. An empty band means every game placed; a visible one is a
> diagnostic, not a rounding error.

### (b) `known-data-issues.md` — three entries

**(b1) The flip history entry**, replacing §6's "Mis-attribution — OPEN"
bullet:

> - **Mis-attribution — CLOSED 2026-08-04 (MLB-159 Exit 1).** `pro_team`
>   now reads the club of the GAME, from the `clubOfGame` field the
>   MLB-129 spike found already sitting on each per-scoring-period split.
>   The 45,059 units of 2025 weight filed under the wrong club (22.25% of
>   the season) and the 23,749-unit `Unattributed` band (11.73%) are both
>   gone: the band measures **0.0 across 2025, 2026 and all-time**, and
>   every one of the 30 MLB clubs renders in every scope.
>
>   Two things did NOT change and are worth stating so nobody re-opens
>   them. The person-level `proTeam` stamp is still written on every
>   extract and still preserved byte-for-byte in RAW — it is the
>   observation record of what ESPN believed and when, and MLB-188's guard
>   exists to stop it being overwritten. And the fix is a re-read, not a
>   re-fetch: the spike pulled 2025's splits a year late and they still
>   showed every deadline trade on the right day, which is why this route
>   was ruled canonical over a crosswalk.

**(b2) The MLB-193 entry** — a new subsection under §6:

> **The residual, bounded and decaying.** 476 player-days (60 players, all
> 2026) produced without a placeable club: ESPN no longer returns them for
> those periods, so the backfill has nothing to read. They are FA-slot rows
> carrying **zero** chart weight, so nothing shipped moves — the chart's own
> scope measures clean, 0 null clubs on rostered rows. Two causes, both
> measured: stale duplicate ESPN ids, and players who have dropped out of
> today's kona window. It **decays**: the gap between a period being lived
> and being backfilled is itself the loss function, so this population
> grows the longer a period waits. Tracked as MLB-193, post-2.0, routed
> through the MLB-129 crosswalk and the MLB gamelog spine — a non-decaying
> source. They stay NULL rather than guessed.

**(b3) The phantom-shadow mechanism** — the paragraph that explains why the
filter is the rule:

> **Why the attribution rule is "producing splits only".** ESPN's
> person-record drift does not stop at the person record: it reaches split
> level. When ESPN moves a player to his incoming club during a transition
> window it emits a split for that club carrying an empty `{}` stats object
> — a phantom that frequently names a club which did not play that day at
> all (36 of 159 stat-less splits name an idle club; the other 123 are the
> "roster-days, not games" artifact). Requiring a non-empty `stats` object
> removes the shadow before any tie-break sees it.
>
> This is not a tidy-up, it is the whole mechanism, and the evidence is
> that it is exactly what separated the two independent reconstructions:
> the spike's sweep had no such filter, so it ranked a phantom equal to a
> real game and let payload order decide — all 13 of its disagreements with
> the backfill are that one shape, and the backfill is right in all 13.
> Majority-by-production survives as a documented DORMANT fallback for a
> genuine same-day two-club day: possible in baseball, and unobserved
> across both ESPN seasons here (0 of 94 multi-club candidates carry two
> producing clubs). Note it is NOT unobservable in general — the CBS book's
> 26 seasons contain 9 real ones (§3).

### (c) `pro_team` column docs

Applied as code, since these are contracts that must travel with the column
rather than drift in a separate file — flagging them here for the same
voice-pass:

- `models/staging/schema.yml` — full game-grain semantics, the attribution
  rule, what NULL means, and that the `proTeam` stamp is deliberately
  preserved in RAW as the observation record.
- `models/intermediate/schema.yml` — the short form, plus "CBS rows carry
  their own capture and are unaffected".
- `models/marts/core/schema.yml` (`fct_player_position_pts`) — "the club he
  wore on the LATEST day he actually appeared inside this matchup period",
  naming both the MLB-168 and MLB-159 traps it is avoiding.
- `stg_box_scores.sql` header — the long-form mechanism note.
- `get_team_affinity_weights` docstring — rewritten. It no longer claims the
  season is "correct" (the over-claim its two predecessors both made); it
  states the rule you can check, and records that Mead now reconciles to
  Baseball Reference on both sides of his move without being tuned to.

---

## 6. Dev build and renders

**`dbt build --target dev` (full 77-model chain): PASS=635, WARN=0,
ERROR=0, SKIP=0**, 543 data tests green, 3m57s. The `dev` target is
`ESPN_FANTASY.ANALYTICS` — the normal working schema; the dev/shipped
distinction in this project is on the SHEETS, and `--prod` is never passed
below.

**Preview (no sheet write) confirms the chart.** 19 tabs rendered; the
affinity block on `Advanced-Standings` carries **exactly 30 club rows and no
Unattributed row**. The single "Unattributed" string left in the book is the
explainer sentence itself.

**Both books rendered end-to-end to their DEV sheets** at branch HEAD —
`espn-main` (default target) and `cbs-bsb` — via
`output/generate_almanac_sheet.py`, no `--prod` anywhere.

### Screenshots — NOT captured, and why

**I could not screenshot the dev sheets.** They are private Google Sheets,
so reaching them needs your logged-in Chrome session, and no Chrome instance
is connected to this session (`list_connected_browsers` → empty). The
sandboxed browser would land on a Google login page rather than the sheet.

Rather than guess, the substitute below is built from the **actual rendered
rows** so you can review the content now, and the dev sheets themselves are
written and waiting for your eyeball. If you want true screenshots, they
take about a minute once Chrome is connected.

---

## 7. The gate

### DuckDB parity — compile-level clean, data-level BLOCKED on a stale copy

**The local DuckDB file predates the backfill, and this matters.**
`data/duckdb/ESPN_FANTASY.duckdb` is dated 08-02; the `clubOfGame` backfill
ran 08-03. Measured directly against that file:

| | entries | `clubOfGame` present | `proTeam` present |
|---|---|---|---|
| 2025 home lineup | 44,725 | **0** | 44,725 |
| 2026 home lineup | 25,959 | **0** | 25,959 |

Its 2026 row count is stale too (25,959 against Snowflake's 27,384). So
building the flipped models there would produce **all-NULL `pro_team` on
every ESPN row** — a stale-copy artifact that would look exactly like a
catastrophic flip defect. Worth knowing before anyone runs it and panics.

**A real data-level A/B needs a RAW refresh first**, which is local-only and
safe but heavy (~2 GB):

```bash
py tools/dump_snowflake_raw_to_parquet.py
py tools/load_parquet_to_duckdb.py
tools/duckdb_run.sh
```

**What IS verified, and it covers the actual risk:**

1. **Compile-level parity.** Both targets compile the changed models. The
   JSON extraction correctly diverges through the existing `json_text`
   macro — DuckDB `(p.value->>'clubOfGame')::string`, Snowflake
   `p.value:clubOfGame::string` — on all three unions. `latest_by` emits
   **byte-identical** SQL on both engines, which is by design: after the
   guard the engines do not disagree.
2. **Semantic parity of the one construct at risk**, verified live on both
   engines rather than inferred — aggregate form, window form, all-null
   groups, and ties. That table is in §1, and it is the check that matters,
   because the UNGUARDED spelling is the one that diverges.

The `stg_mlb__player_game` segfault flake (exit 139, MLB-179) was not
reached, since no DuckDB build was run.

*(the byte-diff table lands here when the run finishes)*
