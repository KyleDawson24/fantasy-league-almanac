# Handoff — Identity Dimension (MLB-81) → Team Pages 1:1

Written 2026-07-14 at the end of the walk-back-laws / records-polish sessions.
Read this + `WALKBACK_PROGRESS.md` (the ledger, bottom-up for recency) before
touching anything. The session memory (`project_cbs_team_pages`,
`project_cbs_league`, `project_cbs_almanac_build`) carries the same facts in
compressed form and loads automatically.

---

## 0. TL;DR

1. **Work in the `modest-montalcini-3af8c4` worktree** (NOT whatever fresh
   worktree your session spawned in), branch `claude/modest-montalcini-3af8c4`.
2. **The gate is MLB-81** (player identity dimension). The Wasted Hall of
   Shame render is HELD behind it, and it should land before the team-pages
   sprint — every surface it touches re-ranks.
3. After MLB-81: **slot-records rework** (primary-position for dark years),
   **HoS render**, then the **team-pages column-exact 1:1** (spec §6).
4. Never sum `fct_player_position_pts.active_pts` for 2004–2020 (§7.3), never
   trust its per-position sums at all (full-credit semantics), and never join
   players by name without reading §5.

---

## 0b. Update — 2026-07-15 (what landed AFTER this doc was written)

The gate and the whole queued records batch below have since shipped; §3/§6.1/
§6.2 are historical now. Current tip `eac15aa` on `claude/modest-montalcini-3af8c4`.
Detail lives in `WALKBACK_PROGRESS.md` + the commits; this is the delta.

- **MLB-81 identity dim — CLEARED** (see `project_cbs_identity_gate` memory).
  Unblocked the HoS render.
- **Records polish batch (`5d700df..eac15aa`)** — all on the CBS dev sheet:
  - **Wasted Hall of Shame** rendered, split **Pitchers | Hitters**, ranked by
    **true wasted** (unrostered + benched), breakdown cells 8pt centered. Sits
    directly right of the Franchise Hall of Fame (buffer col G) at cols **H–K**
    (pitchers) / **L–O** (hitters).
  - **Lineup Slot Records** reworked to **actual lineup slot**: pitcher slots
    span all years; hitter slots are **2026-only** (2001–25 logged "active", not
    the slot — honor the zero-out). U ranks on its own actual 'U' slot, not the
    sum of all hitter positions.
  - **Player all-time records**: Owner column = the franchises the player earned
    with (named, comma-joined), numbers stripped. **All-Time Team Total**
    records rank by **completed-season average**.
  - **HoF line (col F)**: header "Slash | Stat Line (While Active for Listed
    Team)"; **Years of Service (col E) = season count**, centered; pitchers show
    **W-L + labeled ERA/WHIP** (`56W - 31L / 3.19 ERA / 1.20 WHIP`); both
    disciplines use a `||` separator between rate and counting lines.
- **Dynamic team-page hyperlinks (this batch)** — every **standalone**
  active-franchise abbrev cell on the Records page (HoF franchise col,
  leaderboard team cols, HoS "benched most by") links to that franchise's
  team-page tab. Links resolve from live tab **gids at write time**
  (`write_cbs_almanac`, after pass-1), so they survive franchise churn / other
  leagues; **multi-team lists and defunct abbrevs stay plain text**.
  `build_all_tabs` now returns **`(tabs, link_map)`** — both callers updated.
  Verified 102 `#gid=` links on the dev Records tab.
- **Still open**: **team-pages column-exact 1:1** (§6.3) is the remaining
  render sprint, and franchise **continuity entity-scoping / records career
  rollup** is deferred to a dedicated session (see
  `project_cbs_continuity_mlb64` memory). ESPN goldens held throughout.

---

## 0c. Update — 2026-07-17: the team-pages sprint LANDED

§6.3 is DONE and went well past the original spec — see the
2026-07-16/17 rounds in `WALKBACK_PROGRESS.md` (the ledger is the
authoritative account) + the `project_cbs_team_pages` memory. Headline:
CBS team tabs render through the SHARED `build_team_history_tabs` (one
format source, `team_tab_format_specs`); Kyle's team-sheet overhaul
shipped on both leagues (Total/Active/Inactive trio, gold-standard
headers, Other cap + futility chair, the Best Individual Seasons block
over player-season candidates). ESPN goldens re-anchored to the new
shape. Remaining from this doc: §6.4's open Kyle inputs and the
continuity entity-scoping session. Next surface: standings tweaks.

---

## 1. Where to work (mechanics)

- **Worktree:** `C:\Users\kyled\projects\espn-league-manager\.claude\worktrees\modest-montalcini-3af8c4`
  — branch `claude/modest-montalcini-3af8c4`, pushed to `origin`
  (`KyleDawson24/fantasy-league-front-page`, the public portfolio repo).
  Guard every commit with `git symbolic-ref -q HEAD`. Sessions spawn in fresh
  sibling worktrees (this one ran in `...-e08119` at plain `main` — nothing
  lives there); `cd` into the 3af8c4 tree for all work. The shell cwd resets
  between calls — use absolute paths or re-`cd` each command.
- **Python:** the MAIN checkout's venv, shared:
  `C:\Users\kyled\projects\espn-league-manager\.venv\Scripts\python.exe`.
  Scripts that import `output/db.py` need
  `sys.path.insert(0, r"...\modest-montalcini-3af8c4\output")` then
  `db.init(); db.set_league('cbs-bsb')`.
- **Data archive** (CBS HTML/JSON captures) lives in the MAIN checkout:
  `C:\Users\kyled\projects\espn-league-manager\data\cbs_raw\bsb\...` — not in
  worktrees. Probe scripts live in session scratchpads and DO NOT persist;
  rewrite them fresh.
- **dbt:** run from `<worktree>\dbt_league`. Typical rebuild:
  `dbt build --select "cbs_franchises fct_cbs_player_game_attribution+"`
  (~3.5 min). `dbt parse` for a syntax gate.
- **Render:** `output/generate_almanac_sheet.py --league cbs-bsb` (from the
  worktree's `output/` dir) → the CBS dev sheet
  `1itf9U4Wbi_4xEaSkHYR1Mo-TKcFEChs0EmZ01LRKwGw` (`CBS_SHEETS_DEV_ID`).
  **Kyle's eyeball of the dev sheet is the merge gate.** Run renders one at a
  time (they overwrite the same tabs). `--no-sheets --preview-dir <dir>` for
  TSV previews (no colors). ESPN renders with no `--league` flag.
- **Tests:** `pytest tests/ -q` = unit suite (currently 210 pass; warehouse
  tests deselected by default). ESPN golden:
  `pytest tests/ -m warehouse -k almanac_byte_diff` (pinned to 2026 Week 7);
  re-anchor with `REGENERATE_BASELINES=1` **only after** confirming the diff
  is the float-order class (§7.6).
- **Linear:** team `fantasy-league-almanac` only. Open tickets from this arc:
  **MLB-80** (rate-stat thresholds, Kyle to flesh out), **MLB-81** (identity
  dim, the gate, High).

---

## 2. What landed this arc (chronological, with commits)

All on `claude/modest-montalcini-3af8c4`, pushed through `e8f46f3`
(+ this handoff commit).

1. **Track B handoff** (`2b824df`): 2001-02 never-transacted worklist —
   seed `dbt_league/seeds/cbs_early_anchors_backfill.csv` (146 players) +
   legend `CBS_EARLY_ANCHORS_BACKFILL.md` + a **"2001-02 Backfill" tab on the
   dev sheet** (gid 1366638907, dropdown-driven) that Kyle is filling.
2. **Records v3 ESPN mirror** (`5541de0`, `2dd1ec3`): two-scope matrix,
   powder-blue `#f2f7fc` bands, scope labels over their blocks, polarity
   routing (ER/P_H/P_BB → Negative Records as "Most …"), box-score stat order,
   "RBI" not "RBIs", owner-by-abbrev inheritance, " & " multi-owner, ESPN
   column widths, full-sheet **format reset** fix (§7.5).
3. **The universal walk-back laws** (`1c88dcf`, `7d53029`, `e834bca`) — see
   §4. Randy Johnson 2002 (1,112) = best season; Arrieta 1,020 → 991.
4. **Season-grain estimator + adjacent-anchor borrow + sentinel ####**
   (`2e31c08`, `cb5e341`): 2004-20 unweighted days 15.0% → **0.5%**;
   2001-02 zero-event players assume-active on synthetic franchise 9999
   ('####'), fenced from ALL team aggregations; Bonds's 73 (2001) is now the
   HR-season record.
5. **Records polish round** (`f970fe4`, `4de7479`, `d893eac`): rate stats via
   the REUSED ESPN `_hitting_rate`/`_pitching_rate` (no CBS math; only
   AB/HBP/SF/L needed adding to the aggregation), career team records
   active-franchise-only keyed by ABBREV (FULT 13+30 merge), Franchise Hall
   of Fame (top-25 player×franchise, years-of-service stints, no dedup rule),
   Lineup Slot Records v1 (**needs rework**, §6.2), All-Time 'Yrs' column
   dropped (11-col matrix, `_REC_LAST_COL='K'`), orange recency wash removed.
6. **Wasted Hall of Shame** (`d893eac`, fixed in `e8f46f3`): side-by-side
   with the HoF, ranked by wasted = unrostered + benched, "Benched Most By
   FR (total)" column, breakdown ending "N% of career unused".
   **RENDER HELD** — see §3.
7. **ESPN goldens re-anchored twice** (`363bccd`, `53dd884`) — both pure
   float-order (±0.01–0.1 on the same players' rate/ppg cells). Expect a
   third after the MLB-81 rebuild.

Current dev-sheet state: Records tab (157 rows) shows everything through #5;
the HoS on the sheet is the OLD position-fact version (stale — first render
after MLB-81 replaces it).

---

## 3. THE GATE — MLB-81, the player identity dimension

**What broke:** the walk-back joins players by `cbs_name_key(name)` at four
seams (games↔stints, stints↔lineup-intervals, stints↔anchors, anchor
estimator). CBS disagrees with itself about names era-by-era:

- **Smoking gun:** K-Rod's game rows = `Francisco J. Rodriguez`
  (key `francisco j rodriguez`); his Angels-era log rows =
  `Rodriguez, Francisco RP ANA` (key `francisco rodriguez`). No match → his
  peak never attributes. His Mets era logs WITH the initial → attributes.
- **Class size:** 58 record-book players carry middle-initial names;
  114k career pts; **only 48% attributed**. Plus aliases: `Stanton, Mike` ↔
  Giancarlo (2010-11 broken today), Howie/Howard Kendrick, Kendrys/Kendry
  Morales, and Ohtani's `(Batter)`/`(Pitcher)` parentheticals.

**Why not just use ids** (Kyle's Stanton question, audited 2026-07-14):
- txn-log `player_cbs_id`: **0%** of 2001-03 rows, **57%** of 2004-20,
  **100%** of 2021-26.
- Roster-report anchor pages carry **no player links at all** (grepped the
  raw HTML) — names are the only key they serve.
- **Three id spaces:** mlbam (universal stats spine), CBS UI id (e.g. K-Rod
  288981), and `ui-only-<mlbam>` synthetics minted by the record book for
  players **absent from the current-pool API archive** (all departed players
  — the archive only serves current-pool careers). Departed players have no
  crosswalk row, so UI-id → mlbam has no id path.

**The design** (full text in MLB-81): `dim_cbs_player_identity`
(`cbs_ui_id ↔ mlbam_id ↔ {name variants}`), built ONCE — pinned by real ids
where they exist (2021+ fully; 2004-20 partially, which also validates the
name-matched remainder), season+team-scoped name resolution only where ids
don't exist (2001-03 + anchors), alias/variant rows as a seed (one-row fixes
forever). Rewire the four joins to id equijoins against the dim; keep the
ambiguity machinery (`is_ambiguous_name`, discipline-preference QUALIFY,
`attribution_contested`) for genuine collisions — note there are TWO real
Mike Stantons inside the league window (reliever 2001-07, Giancarlo-as-Mike
2010-11; non-overlapping, so season-scoping resolves them).

**Canaries after rebuild:** K-Rod's Angels years attribute; the
middle-initial class rises 48% → ~95%+; Stanton 2010-11 attributes; Ohtani
halves stay split; Verlander unchanged (he was never broken — see §7.3's
lesson). **Expected fallout:** records/HoF/HoS re-rank, full CBS chain
rebuild, ESPN golden float re-anchor, CBS dev re-render.

---

## 4. The walk-back laws + fidelity map (settled, do not re-litigate)

**Law 1 — discipline scopes scoring.** Hitters can't occupy pitching slots
and vice versa, so: active hitter → hitting points ONLY; active pitcher →
pitching points ONLY. Every pitcher's batting line is phantom (Arrieta 2015
+29 was one instance). Ohtani stays consistent because CBS splits him into
two single-discipline entities (900 Batter / 901 Pitcher). Implemented in
`int_cbs__player_game_points`.

**Law 2 — the transaction log is a state machine.** Every event proves
membership (team, date); `from_slot` classifies the state BEFORE the boundary
(back to the prior boundary or membership start), `to_slot` the state AFTER
(until the next). A player whose FIRST event of a season is a lineup move was
on that roster since **the season's earliest recorded transaction**. Found +
fixed along the way: stints seeded only from add/trade/drop (slot-movers like
2001 RJ vanished — `lineup_opening`/`lineup_evidence` channels added), and
`to_slot` had NEVER parsed (broken WHEN gate + a regex Snowflake rejects).

**Era fidelity map (membership | activity):**

| Era | Membership | Active/inactive |
|---|---|---|
| 2001-02 | log-only (Law 2); zero-event players → sentinel #### until backfill | lineup log, exact 1/0 |
| 2003 | year-end anchor + log | lineup log, exact 1/0 |
| 2004-20 | anchor + log (solid) | **estimated**: Start%÷Own% (global player-season stats; Start% ≤ Own% verified on all 9,969 anchor rows); season-grain join; nearest-season adjacent borrow; residue 0.5% |
| 2021-25 | anchor + log | lineup log again (the `all` filter carries Benched/Activated verbs — NOT "Moved"), exact 1/0 |
| 2026 | daily capture | captured |

**Provenance enums** (accepted-values tests updated): `captured`,
`reconstructed_day`, `estimated_startshare`, `estimated_adjacent`,
`estimated_membership`, `sentinel`; state_source adds `adjacent`, `sentinel`.

**Anchor field census** (per year-end roster reports): rates absent 2003,
present 2004-25; eligibility list 2003-04 ONLY; mlb_status all years; the
page's "Pos" column is the season-end LINEUP SLOT (vocab shifts 2020+: U and
bare P appear) and lands in `roster_pos`; `primary_pos` is parsed separately.
Own%/Start% are season-cumulative, not snapshots.

---

## 5. Identity/id facts (don't re-derive)

- `player_key` (CBS rows) = `cbs_player_id` verbatim, including
  `ui-only-<mlbam>` synthetics. The synthetic's number IS the MLBAM id.
- The record book (`int_cbs__player_season_stats`) is UNIVERSAL-sourced
  (MLB API × crosswalk), 2001+ only, complete for every crosswalked player —
  so "career total" includes seasons the league never rostered. Its
  `CALCULATED_POINTS` is our engine's number, not CBS's (`PLATFORM_POINTS`
  is CBS's own, in `stg_cbs__player_season_stats`, which carries NO batting
  rows for pitchers — part of the Law-1 proof).
- CBS name forms differ by page: record book uses disambiguation initials
  ("Jose B. Reyes"), the log mostly doesn't, roster reports have their own
  drift. `cbs_name_key` strips periods/suffixes/pos-team tokens and flips
  Last,First — it does NOT strip middle initials (by MLB-81 design choice it
  will become a dim-build variant rule instead).

---

## 6. Queued work (in order, after MLB-81)

### 6.1 Wasted Hall of Shame — finish (spec LOCKED, render held)
Built correctly on the daily fact (`e8f46f3`). Spec: physically right of the
HoF, aligned with the All-Time block; columns
`Rank | Player | Benched Most By (abbrev + total benched by that team) |
Wasted Points | Breakdown`; wasted = unrostered + benched; breakdown =
"X unrostered · Y benched · Z active · N% of career unused". 2004-20 benched
is the estimator's complement (Kyle confirmed OK). After MLB-81, re-run and
eyeball the top-10 — it should stop being K-Rod-class artifacts.

### 6.2 Lineup Slot Records — REWORK (Kyle's model chosen)
Current v1 (on the sheet) reads `fct_player_position_pts`, which
**full-credits every eligible position** (Zobrist 2012: 2B=520.7 AND
OF=520.7) — inflated, per Kyle "worse than wrong." His chosen model:
- **Dark years (2004-20): PRIMARY POSITION only** (one position per
  player-season). He explicitly rejected full-credit; the
  eligibility-SPREAD idea (divide by eligible slots) was considered and
  deferred (inflates DH/U/OF aggregates).
- **2021+ (and 2001-03): actual slots** where the lineup log / captures
  know them.
- **Template = the CURRENT league structure, dynamic:** C/1B/2B/3B/SS,
  OF×3, U, DH, P×9 (2026 active-slot census: OF x48, P x144 across 16
  teams → 3 OF + 9 P per team). Note the position fact has no 'U' — U is
  the any-hitter slot; fill rule needs Kyle's confirm (likely best
  remaining hitter, ESPN-UTIL-style).
- **Layout asks:** the 2004-20 caveat moves to the cell right of the
  "All-Time Total" header (his "I77"); Details = player rows get the
  normal stat-line, team rows get top contributors descending; left =
  best player-season by active pts at the slot, right = the active
  FRANCHISE with most all-time active pts from the slot.
- IMPORTANT nuance: full-credit eligibility is CORRECT for the optimal-
  lineup SELECTORS (candidacy — pick the best assignment, each player used
  once) and only wrong when SUMMED. Don't "fix" the team-page/board
  machinery while fixing the records sums.

### 6.3 Team pages — column-exact 1:1 (the sprint this handoff feeds)
Semantics are LOCKED (memory `project_cbs_team_pages` + `build_team_tab`
docstring): both sides are THIS franchise's OWN best lineup (left current
season, right all-time cumulative, "all pieces at once"); starters by active
pts for this team; bench + others by TOTAL pts while on this roster; players
recur across pages (Freeman on BP-current AND CAL-all-time).
**Target fixture:** `tests/fixtures/almanac_v1_1_0/CAL.tsv` (ESPN's shape).
Remaining gaps:
- **Tm columns (A & P, small font):** where the player is rostered NOW —
  `*` this page's team, abbrev another team, blank unclaimed.
- **MLB `Team` column** (pro team abbrev — already on the facts).
- **Slash-line columns** (`Avg|W-L-Sv`, `OBP|ERA`, `Slg|WHIP`, `HR|K`,
  `SB|BB` with the hitter/pitcher sub-header rows): the components are ON
  the shared fact (h/ab/hbp/sf/tb/er/outs/p_h/p_bb — that's how the Records
  rate stats worked); pass through ESPN's `_hitting_rate`/`_pitching_rate`/
  `_all_league_slash_line` — NO CBS math.
- **Bench/IL Points column** (CBS analog = inactive/reserve points; CBS has
  RS, not a separate IL — adapt the label honestly).
- **"Other" overflow section**, sorted by total pts, **capped at 100** on
  the all-time side.
- Years of Service stays on the all-time board.

### 6.4 Open Kyle decisions / inputs
- **Dead-team per-era owners:** 16 active franchises have owners; 14
  truly-defunct abbrevs (WOL, NYN, CYB, EBB, DOB, HAY, CSC, BENT, TGUN,
  BOMB, HGH, DAWG, TYR, VCF) have none anywhere. Kyle said he's "working in
  the background on getting that all keyed" — expect a seed/mapping from him.
- **MLB-80** thresholds (interim: MLB batting/ERA qual rules for players,
  no team minimums).
- **U-slot fill rule** (§6.2).
- **Track B backfill sheet** (§2.1): when filled, ingest → synthesize
  2001-02 anchors → re-run walk-back → the sentinel retires itself and the
  Negative-Records anchor gate lifts automatically (it keys on anchor
  presence in `stg_cbs__ui_rosters`).

---

## 7. Gotchas that burned us (verbatim lessons)

1. **`db.py` lowercases result keys** — access `r['calculated_points']`,
   never `r['CALCULATED_POINTS']`. `rows` is a Snowflake reserved word.
2. **Never name a scratch script `platform.py`** — it shadows the stdlib and
   breaks `snowflake.connector` imports with a circular-import error.
3. **`fct_player_position_pts` has two active lenses:** `active_pts` is
   populated ONLY where the day's state is known (2001-03, 2021-26);
   `weighted_active_pts` carries the 2004-20 estimator. Summing the former
   for the estimated era produced the false "Verlander 87% unrostered."
   And per-position rows FULL-CREDIT every eligible position — never sum
   across positions per player.
4. **The record book's career total ≠ league-rostered production** — the
   difference is genuine unrostered time PLUS any attribution gaps; after
   MLB-81 the gaps shrink to ~real.
5. **`worksheet.clear()` clears values, NOT formats** — the style pass
   starts with a full-sheet `userEnteredFormat` reset or colors stack
   forever. Write Records/team tabs with `USER_ENTERED` or `=HYPERLINK`
   renders as text. Sheets write quota: one style batch per tab, 70s backoff.
6. **ESPN golden float class:** any rebuild of the shared
   `fct_player_position_pts` TABLE flips a handful of rounding-boundary
   rate/ppg cells (3.74↔3.75 etc.) on ESPN tabs. Verify the diff is
   same-players ±0.01-0.1 numeric-only, then `REGENERATE_BASELINES=1`.
   The byte-diff harness pins `--season-year 2026 --matchup-period 7`; an
   unpinned preview renders the LIVE week and looks like massive fake drift.
7. **Transaction log:** the `all` filter (not `all_but_lineup`) carries the
   lineup verbs, which are "Benched"/"Activated"/compounds in the modern era
   — a text-search for "Moved" finds ~nothing. `?print_rows=9999` returns a
   whole season in one GET (Kyle-verified on both filters; documented in
   `extract/cbs_ui_capture.py`). Museum rule: read-only, always.
8. **Don't poll background tasks** (notifications arrive); launch multi-hour
   sweeps DETACHED via PowerShell `Start-Process` (Bash background dies with
   the session). dbt builds (~4 min) are fine as normal background tasks.
9. **Negative Records gates** (all self-healing, don't hardcode years):
   full-length seasons (max team-total ≥ 60% of median — drops 2020 COVID),
   closed seasons (`ui_standings` presence — drops live 2026), anchored
   seasons (`ui_rosters` presence — drops 2001-02 until backfill).
10. **Two-way players are two CBS assets** (900/901); the crosswalk scope
    guard keeps their stat groups apart; a "player" here is a CBS asset,
    not an MLB person.

---

## 8. File map (what this arc touched)

- `dbt_league/models/marts/core/fct_cbs_player_game_attribution.sql` — the
  attribution heart: laws, estimator joins, adjacent borrow, sentinel CTE.
  Header documents every provenance tier. **MLB-81 rewires its name joins.**
- `dbt_league/models/intermediate/int_cbs__roster_stints.sql` /
  `int_cbs__lineup_intervals.sql` — Law-2 seeding (lineup_opening /
  lineup_evidence channels, slot-state machine). Also name-join sites.
- `dbt_league/models/intermediate/int_cbs__player_game_points.sql` — Law 1
  (discipline arbiter).
- `dbt_league/models/*/schema.yml` — provenance/state_source enums.
- `dbt_league/seeds/cbs_franchises.csv` — includes sentinel row
  `9999,####`. `cbs_early_anchors_backfill.csv` — Track B worklist.
- `output/cbs_almanac_sheets.py` — the whole CBS renderer: records data
  (`get_cbs_records_data`), rate specs/quals, HoF/HoS, slot records v1,
  fences, team tabs, write layer. ~everything in §2 lives here.
- `output/almanac_render.py` — ESPN helpers we REUSE (never fork):
  `_hitting_rate`, `_pitching_rate`, `_all_league_slash_line`,
  `_bref_player_cell`.
- `WALKBACK_PROGRESS.md` — the ledger; append per round, mark resolved.
- `extract/cbs_ui_capture.py` — CBS UI capture + API-idiosyncrasy notes.

## 9. Definition of done for the next session

1. MLB-81 dim built, four joins rewired, rebuild green (dbt tests + unit
   suite), canaries pass (§3), ESPN goldens re-anchored (float class only),
   CBS dev re-rendered.
2. HoS render unheld; top-10 sane; Kyle eyeballs.
3. Slot-records rework per §6.2 (or explicitly parked with Kyle's sign-off).
4. Then open the team-pages sprint per §6.3 against `CAL.tsv`.
