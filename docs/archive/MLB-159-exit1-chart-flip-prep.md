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

### The two-club days: SUSPENDED GAMES, not Youngbloods

**CORRECTION (Kyle, 08-04).** An earlier draft of this section called these
"9 real Youngbloods". That was wrong, and wrong in a way worth recording:
a Youngblood — hits for two clubs in two cities on one day, Joel Youngblood
1982-08-04 — is a once-ever event that predates this league. Nine of them in
one league's player pool is not credible on its face, and the claim came
from verifying a *structural shape* (two `game_pk`s, two `team_id`s) and
then labelling it with a term that means something far stronger.

**There are ZERO Youngbloods here.** Enumerated with names:

| date | player | real club that day | second club | traded there |
|---|---|---|---|---|
| 2008-04-28 | Ken Griffey Jr. | Reds | White Sox | Jul 31 |
| 2009-05-05 | Nyjer Morgan | Pirates | Nationals | Jun 30 |
| 2019-05-19 | Jake Diekman | Royals | Athletics | Jul 27 |
| 2021-04-11 | Javier Báez | Cubs | Mets | Jul 30 |
| 2021-07-21 | Daniel Hudson | Nationals | Padres | Jul 30 |
| 2021-07-21 | Jake Marisnick | Cubs | Padres | Jul 30 |
| 2021-07-21 | Adam Duvall | Marlins | Braves | Jul 30 |
| 2021-07-21 | Jorge Soler | Royals | Braves | Jul 30 |
| 2024-06-26 | Joey Loperfido | Astros | Blue Jays | Jul 2024 |
| 2024-06-26 | Danny Jansen | Blue Jays | Red Sox | Jul 2024 |

In every row the second club is the one the player was traded to **later
that season**, so he cannot have played for it on that date.

**The mechanism is suspended games.** A suspended game keeps its ORIGINAL
date; when it resumes weeks later, players acquired in the interim appear in
a game dated before they joined. Soler's log proves it — game_index 86 =
07-21 Royals, 87 = 07-21 Braves, then 88-92 = 07-23 through 07-27 **still
for Kansas City**. And the Braves and Padres each carry TWO `game_pk`s on
2021-07-21 (633224 and 633250). Padres @ Braves was suspended that day,
which is why four of these cluster on one date: all four changed clubs at
the same deadline.

### Danny Jansen is the real one, and the earlier draft had it backwards

`mlbam 643376` shows two clubs inside a SINGLE `game_pk` (746942), listed as
his own opponent. The earlier draft called that "a data defect". It is not:
the 2024-06-26 Blue Jays-Red Sox game was suspended with Jansen at bat for
Toronto, he was traded to Boston, and when it resumed on 08-26 he played for
Boston. **He is the only player in MLB history to appear for both teams in
the same game.** The shape that looked most like corruption was the one
genuine curiosity.

### What this does and does not threaten

**Club attribution is CORRECT in all ten.** Soler really did play that game
for Atlanta, so the affinity chart credits the right club. There is no club
defect here, and the CBS chart's per-game attribution handles it properly.

**The DATE is what is wrong, and that is the real exposure.** Production
from a resumed game is filed on the suspension date, so
`fct_cbs_player_game_attribution` joins it to whoever rostered the player on
the earlier date rather than the date he actually produced.

**These ten are only the VISIBLE subset.** Any suspended-and-resumed game
mis-dates its production, trade or no trade; the club change is merely what
made these detectable. That general class is **not sized here**.

Impact on what is visible: 5 of the 10 carry CBS chart weight, ~8 units of
`active_weight` across 26 seasons — negligible, and no club is misattributed.
The ESPN side sources club per scoring period rather than from the MLB
spine, so it likely files these on the resumption date instead; **not
verified**.

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

### The flip verified against something outside the warehouse

**Curtis Mead reconciles to Baseball Reference to the unit**, and he was not
tuned to. Post-flip, at the daily grain:

| club | PA | player-days | scoring periods |
|---|---|---|---|
| Washington | **327** | 87 | 7–123 |
| Detroit | 39 | 12 | 2–16 |
| Boston | **2** | 1 | 125 only |

Baseball Reference has him at 327 for Washington and 1 game / 2 PA for
Boston. **Before the flip the warehouse credited Boston with 20** — the 18
extra were Washington games in the same matchup period as the move,
relabelled wholesale (`known-data-issues.md` §6 documents that as the
worked example). Boston is now exactly one day, on the right day.

**And the buried history comes back.** Player-seasons carrying more than one
club:

| season | before the flip | after |
|---|---|---|
| 2025 | **0** of 1,236 | **158** |
| 2026 | 66 (inflated by 10 FA↔club transitions) | **57** |

2025's zero was the tell that a one-pass backfill had stamped the whole year
with a single club: not one in-season trade was represented, league-wide.
2026's 57 matches the handoff's independently-measured club-of-game figure
exactly.

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

### The gate results

| gate | result |
|---|---|
| `dbt parse` | **clean**, both targets |
| `dbt build --target dev` | **PASS=635, ERROR=0, SKIP=0** (543 data tests) |
| pure suite | **296 passed** (282 inherited + 14 new pins), 0 failed |
| byte-diff, both books | **4 failed, 13 passed** — the SAME four as before this work (re-run clean after the env fix: still 4/13, 7m49s) |
| DuckDB parity | compile-level clean; data-level blocked on a stale local copy |

**No golden was re-anchored.** `REGENERATE_BASELINES` was explicitly cleared
before the run.

### Per-file diff attribution — and the thing that blocks it

The four failures are the same four the previous session measured *before*
the backfill (handoff §5: "4 failed, 13 passed... same test names, no
movement in either direction"). Their names:

`test_almanac_tsv_matches_baseline` · `test_cbs_almanac_tsv_matches_baseline`
· `test_summary_bbcode_matches_baseline` · `test_records_report_bbcode_matches_baseline`

**The harness reports only the FIRST differing line per file, and on every
ESPN tab that first line is week-17 data drift — not the flip:**

| file | first diff | attribution |
|---|---|---|
| 13 team tabs (AAA, BP, CAL, CHIN, CYCL, FNA, FUBB, GPGP, HANG, HH, LAW, NPNP, SMEL) | "Optimal Lineups, through Jul 26, 2026" → "through Aug 2, 2026" | **known-stale** — a week of data landed |
| `Advanced-Standings.tsv` | standings order (HANG/WALK swap), win pct .615 → .6 | **known-stale** |
| `Draft-Recap.tsv` | McGonigle 308.0 → 340.6 | **known-stale** |
| `Matchup-History.tsv` | 641 → 655 lines, Week 16 → Week 17 | **known-stale** (14 new rows = one week × 14 teams) |
| `Records.tsv` | 96 → 124 lines, best team total 401.3 → 413.4 | **known-stale** |
| `baseline_records_report.txt` | `Hits: 89 … Week 10` → `90 … Week 17` | **known-stale** — the exact drift handoff §5 names |
| `Home.tsv` | *(first run)* `leagueId=1234567890` → `leagueId=0` | **test env leak — found and fixed, see below** |
| `Home.tsv` | *(clean re-run)* line 32, Cal Raleigh 645 → 649 pts | **known-stale** |

After the env fix the ESPN run carries **zero** occurrences of `leagueId=0`,
`Records.tsv` shows the correct id on both sides, and `Home.tsv`'s first diff
moves from line 6 to line 32 — i.e. every remaining first-diff in both books
is week-17 data drift.

**The CBS book gives the cleanest evidence in the whole gate.** Every CBS
diff is 2026 stat accrual — game counts, points, and the coverage
percentages in the tab headers ("captured live (18%)" → "(19%)",
"reconstructed from the transaction log (72%)" → "(71%)"). And in every
diffed line **the club column is byte-identical**: William Contreras still
MIL, Bryce Harper still PHI, Ben Rice still NYY, Jonathan India still KC,
Brandon Lowe still PIT. Only the numbers beside them moved.

That is byte-level confirmation of the design claim "CBS rows untouched" —
stronger than the code-reading argument, because it is measured on the
rendered output of all 14 franchise tabs.

**So the flip's own ESPN diffs cannot be attributed byte-wise tonight: they are
buried underneath two weeks of drift.** The fixture is anchored ~Jul 27 and
week 17 has since landed, so the first diff on every file fires before the
affinity chart is ever reached. This is not a new problem and not caused by
this work — it is the stale-golden condition already awaiting your re-anchor
decision — but it does mean the ordering matters: **re-anchor first, THEN the
byte-diff can show what the flip actually changed.**

What stands in for it, and is arguably stronger, is the data-layer
measurement: Unattributed 0.0 in all three scopes against 11.73%/0.02%
before, 30 clubs rendering in every scope, Mead reconciling to Baseball
Reference on both sides of his move, and 2025's mid-season trades going from
0 to 158 player-seasons.

### The flip's true diff, computed instead of byte-diffed

Since the goldens cannot show it, the same question is answered directly:
score every active-slot row under BOTH attributions (RAW still carries both
keys) and compare at the chart's own grain.

| season | total weight | weight that MOVED club | % | Unattributed before → after |
|---|---|---|---|---|
| 2025 | 202,547 | **45,059** | **22.25%** | 23,749 → **0** |
| 2026 | 129,456 | 272 | 0.21% | 30 → **0** |

**45,059 / 22.25% reproduces `known-data-issues.md` §6's independently
measured figure to the unit** — the number that section calls "filed under
the wrong club". That is the declared affinity diff, and it is now a
measurement rather than a projection.

### RESOLVED: `leagueId=0` was a test env leak, and it had teeth

The byte-diff's ESPN render emitted `leagueId=0` into every ESPN box-score
hyperlink where the fixture carries the real id. **I could not reproduce it
outside the test**, and chased it rather than hand-waving:

| probe | `LEAGUE_ID` seen |
|---|---|
| my direct preview's TSVs | **1234567890** (correct) |
| `db.init()` in-process | 1234567890 |
| child subprocess with the test's exact `env=dict(os.environ, …)` | 1234567890 |
| same child under the PowerShell `Start-Process` launcher | 1234567890 |
| ambient / user / machine env | not set anywhere |
| `tests/conftest.py`, `pytest.ini` | no env manipulation |
| `SUPPRESS_UPDATED_STAMP` | only blanks the Updated stamp (`almanac_logic:569`) |

The reproduction run settled it: the byte-diff's **exact** command, anchor
and all, emits the correct id outside pytest. So the fault was in the pytest
PROCESS, and the cause is a module-level line in a file the previous session
added:

```python
# tests/test_extract_club_of_game.py
os.environ.setdefault("LEAGUE_ID", "0")
```

The chain, verified end to end:

1. pytest never calls `load_dotenv()`, so `LEAGUE_ID` is unset in its
   process no matter what `.env` holds;
2. collection imports every test module, so this ran — and the "real value"
   the `setdefault` was written to protect **is never present at that
   point**, so it always fired;
3. `test_almanac_byte_diff` spawns its render with
   `env=dict(os.environ, ...)`, so the child inherited `"0"`;
4. the child's own `load_dotenv()` could not undo it — **load_dotenv does
   not override an existing variable**.

**This had teeth, and that is why it was fixed rather than noted.** The
documented re-anchor command is
`REGENERATE_BASELINES=1 pytest tests/ -m warehouse` — precisely the
invocation that leaks. Re-anchoring through it would have written
`leagueId=0` into the golden corpus permanently, and every ESPN box-score
link in the almanac would have pointed at league 0 from then on. The
re-anchor decision was the very next thing on the list.

Fixed by calling `load_dotenv()` before the `setdefault`, which restores the
comment's actual intent: a real value wins where one exists, a fresh clone
still gets its placeholder. Pure suite unchanged at 296. The byte-diff was
then re-run from a clean environment for the attribution above.

**Not flip-related** — nothing in this wave touches league identity or link
construction. It is an inherited test-infra defect, surfaced only because
the diff attribution forced the question.

---

## 8. What needs your call

1. **The golden re-anchor, and its ORDER.** The fixtures are anchored ~Jul
   27 and week 17 has landed, so every first-diff is drift and the flip's
   own changes are invisible underneath it. Re-anchor first, then the
   byte-diff becomes readable. **Re-anchor only from the fixed branch** —
   the `leagueId=0` leak is fixed here and nowhere else.
2. **The explainer wording** (§5a) — three drafts, yours to voice-pass. The
   first is applied so the dev sheets aren't showing a false sentence.
3. **`known-data-issues.md`** — three drafted entries (§5b), not applied.
   They are shipped-doc prose, so the voice is yours.
4. **The Best Individual Seasons Team column** (§4): its comment justifies
   showing YEAR because "pro_team is only season-accurate on the CBS side".
   Now stale for ESPN, still true for CBS. Product call, not a defect.
5. **Suspended-game dating** (§3, corrected): production from a resumed game
   is filed on the SUSPENSION date, so it attributes to whoever rostered the
   player then rather than when he actually produced. Club attribution is
   unaffected. The ten two-club rows are only the visible subset — the
   general class is unsized. Worth a ticket to size it? (Jansen is NOT a
   defect; he is the one real same-game-both-teams case in MLB history.)
6. **DuckDB data-level parity** needs a RAW refresh first (§7) — the local
   file predates the backfill.

**Not done, and deliberately:** no push, no Linear write, no golden
re-anchored, no shipped-sheet write, no `--prod` anywhere, RAW read-only,
the MLB-188 guard and the `_BAK` snapshot untouched.

### One scope call I made without you

I fixed the `LEAGUE_ID` leak (§7) rather than only reporting it. The
reasoning: correct per-file diff attribution was a deliverable of this
session and was impossible while the leak was in place, and the next action
on the list — the re-anchor — would have written `leagueId=0` into the
corpus permanently. It is one self-contained commit and trivially
revertible if you disagree.
