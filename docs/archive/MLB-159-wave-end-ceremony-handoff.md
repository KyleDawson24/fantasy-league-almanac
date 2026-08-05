# Wave-end ceremony — merge, the seventh class, and the kept re-anchor

**Run:** 2026-08-04, single session · **Branch:** local `main`, nothing
pushed. Kyle runs `git log origin/main..main`, names the set, and pushes
by hand.

Lands in `docs/archive/` per the MLB-154 root-curation rule.

---

## 1. The merge — a fast-forward, and there was nothing to resolve

The kickoff expected conflicts in the render orchestration and
`almanac_data` / `almanac_logic`, since both branches touched display
code. **There were none, and the reason is topological rather than
lucky:** `mlb-163-season-history` was authored *on top of*
`exit1/chart-flip`, not beside it. `b317dd1`'s parent is `156e080`, the
flip's tip, so the two never diverged and the whole range is linear.

```
main 0876612
  └─ exit1/game-level   (4 commits)
       └─ exit1/chart-flip  (13 more)
            └─ mlb-163-season-history  (5 more)   = 22 commits
```

Both merges were `--ff-only`. **Zero conflicts, so there is no
resolution table** — whatever reconciliation the two intents needed had
already been done when 163 was written against the flipped code.

`origin/main..main` is now **26 commits** (22 inherited + 4 from this
session). Local `main` only; no push at any point.

### Preflight deviations, both benign

- **The WIP file is gitignored, not untracked** (`.gitignore:157`), which
  is why `git status` reads clean rather than showing it. Its 2 failures
  are still there and still pre-attributed.
- **MLB-188 carries four ruling comments, not five.** All four were read,
  plus the issue description. Nothing in them contradicts the handoffs.
  The acceptance comment names a *four*-class golden attribution; the
  kickoff's ⑤ and ⑥ are later additions, consistent rather than
  conflicting.

---

## 2. THE SEVENTH CLASS — a defect, not a class

The attribution pass found a diff that fits no declared class: club
labels on the ESPN team tabs going **blank**.

| scope | players | blank club | share |
|---|---|---|---|
| all-time | 897 | **476** | **53.1%** |
| current season | 646 | **326** | **50.5%** |

Rutschman read blank where he had read `Bal`. On `AAA.tsv` alone, 39 club
cells changed and **30 of them went blank**.

**The mechanism.** The flip made `pro_team` the club of the *game*, which
means it is NULL on every day a player did not appear. Any consumer that
pins the label to one particular row therefore prints an empty club for
anyone whose last day in scope was a rest day. `latest_by`'s guard exists
for precisely this — its own docstring says so — and the MLB-168 sweep
applied it to `generate_summary` and `generate_season_report` while
missing three sites in `almanac_data.py`.

**Why the sweep missed them.** The flip's §4 consumer enumeration cleared
`mart_daily_roster_snapshot` as *"passthrough — no aggregation at all —
safe."* That is true of the **mart** and says nothing about its
**readers**, which is where all three defects live. Two are the row-pick
shape the sweep already knew about; the third is a different shape
entirely (a single-day pin), which is why widening the audit rather than
patching the two known ones was the right call — it was found only
because the audit stopped keying on shape-I-already-know.

---

## 3. The full `pro_team` consumer audit

Every occurrence across `output/`, `dbt_league/` and `extract/` — 21
files, 86 code-line occurrences — classified by shape. Comment and
docstring mentions are excluded.

### dbt layer

| site | shape | status |
|---|---|---|
| `stg_box_scores.sql:122/149/179` | **origin** — reads RAW `clubOfGame` | n/a (the source) |
| `stg_cbs__rosters.sql:40` | **origin** — CBS's own capture | safe-by-shape (CBS untouched) |
| `int_player_daily.sql:273/390` | passthrough | safe-by-shape |
| `int_cbs__player_daily.sql:120` | aggregation `max(pro_team)` | safe-by-shape — CBS, already daily grain; measured 0 groups carry two clubs |
| `int_cbs__player_daily.sql:198/259` | passthrough | safe-by-shape |
| `fct_player_daily_performance.sql:56` | passthrough | safe-by-shape |
| `fct_player_position_pts.sql:84` | passthrough | safe-by-shape |
| `fct_player_position_pts.sql:169` | collapse day→period | **guarded** (`latest_by`, MLB-168) |
| `mart_daily_roster_snapshot.sql:42` | passthrough | safe-by-shape — **but see its readers below** |

### output layer — the collapsing/pinning consumers

| site | function | shape | status |
|---|---|---|---|
| `almanac_data.py:430` | `get_optimal_team_candidates` | collapse period→career | **guarded** |
| `almanac_data.py:468` | `get_optimal_season_candidates` | collapse period→season | **guarded** |
| `almanac_data.py:~511` | `get_team_player_season_stats` | **row-pick** | **FIXED NOW** |
| `almanac_data.py:~1692` | `get_team_roster_history_stats` | **row-pick** | **FIXED NOW** — the live regression |
| `almanac_data.py:~2008` | `get_current_team_roster_stats` | **single-day pin** | **FIXED NOW** — latent; the function is unreferenced today |
| `generate_summary.py:288` | `player_meta` | row-pick | **guarded** (window form) |
| `generate_season_report.py:375` | `player_meta` | row-pick | **guarded** (window form) |
| `cbs_almanac_sheets.py:2379` | CBS player_key collapse | aggregation `MAX_BY(pro_team, game_date)` | safe-by-shape — CBS, already latest-by-date |
| `cbs_almanac_sheets.py:2651` | CBS player_key collapse | aggregation + `COALESCE(..., MAX(current_club))` | safe-by-shape — fallback guarded |

### output layer — non-collapsing

| site | shape | status |
|---|---|---|
| `almanac_data.py:1232` (`get_team_affinity_weights`) | **filter/bucket** at daily grain, no per-player collapse | safe-by-shape — NULL/`FA` → `Unattributed` by design |
| `cbs_almanac_sheets.py:2627–2630` | **filter** (`DISTINCT` / `IS NOT NULL` lookup) | safe-by-shape |
| `almanac_data.py:1476/1550` | live ESPN API path (Trades tab `_PRO_TEAM_MAP`) | out of scope — not the warehouse column |
| `almanac_render.py:605/668/830/1172/1193/1488` | **render-read** `row.get('pro_team') or ''` | safe-by-shape — NULL-safe by construction |
| `cbs_almanac_sheets.py:2800` | render-read | safe-by-shape |
| `almanac_logic.py:1463` | render-read (affinity club) | safe-by-shape |
| `almanac_logic.py:495` | default `None` | safe-by-shape |
| `almanac_logic.py:2305` | **overwrite** — cell reused for overflow text | assigns, never reads |
| `almanac_logic.py:2387` | **overwrite** — Team column doubles as YEAR in Best Individual Seasons | assigns, never reads. This is why site `:511`'s defect never rendered: its label is replaced before display. Guarded anyway — the display ruling is a product call that could change back. |
| `extract/extract.py`, `extract/mlb_crosswalk.py` | writes the person-level stamp | out of scope — the observation record, deliberately preserved (MLB-188) |

**Totals: 3 fixed now · 5 already guarded · the rest safe-by-shape or out
of scope.** No unguarded collapsing or pinning consumer remains.

### For MLB-192

This shape belongs on that ticket's board, with one correction to how it
is framed there. MLB-192 is about traps *the goldens structurally cannot
see*. **This one was fully golden-visible** — 441 cells moved. What
nearly shipped it was not invisibility but that a plausible declared
class could have absorbed it; the thing that caught it was the ceremony's
rule that every diffed cell attribute to exactly one class and anything
left over is a STOP. That is a **process** guard, and it worked. The
code-level guard MLB-192 contemplates would have caught it earlier and
without a human in the loop, which is the argument for building it.

---

## 4. The attribution table

**One kept anchor.** The first run was killed mid-flight and discarded:
Kyle picked explainer wording after it started, and that text is
golden-visible, so finishing it would have anchored a corpus that
disagreed with the shipping code. A second run was discarded for the
seventh class. The corpus below is the third run and the only one kept —
`24 passed, 0:06:43`, from the fully fixed checkout.

Corpus: **1 file added, 41 changed, 3 unchanged.**

| class | signature | found |
|---|---|---|
| ① 163 | new `Season-History.tsv`, both Home navs | **400 lines · 395 data rows · 25 seasons · 39 columns.** CBS Home gained `Season History / Every team-season since 2001, as awarded.`; ESPN nav ends `... Draft Recap, Matchup History`, CBS `... Draft Recap, Season History` |
| ② Exit-2 | affinity rows restored | **no discriminating signature at this anchor** — see below |
| ③ drift | accrual | `Matchup-History` 641→655 (+14 = one week × 14 teams), roster days 124→131, `Records` best-team total moved, CBS coverage % in tab headers, CBS row re-sorting |
| ④ flip | club re-attribution | **label-side only here**: Romano `FA`→`LAA`, Castellanos `FA`→`SD` — the extract-day FA artifact gone |
| ⑤ 168 labels | traded players | Mead `Wsh`→`Bos`, Doval `NYY`→`Pit`. Small, 2026-heavy, as declared |
| ⑥ Wasted tweak | breakdown reorder | **100 of 100** fragments descending with `active` pinned last, both books |

**Nothing outside the six.** The one thing that was outside them was the
seventh class in §2, and it is fixed rather than absorbed.

### Two check-values that do not mean what the kickoff expected

**②/④'s affinity signature is not testable against this corpus.** The
affinity block reads **30 club rows and no `Unattributed` row in BOTH the
old and the new fixtures**. The byte-diff pins **2026 Week 7**, and the
Unattributed band was a 2025 phenomenon — 11.73% there against 0.02% in
2026. So "Unattributed rows: GONE · 30 clubs every scope" is *satisfied*
but *vacuous* here: it was already true before the flip. The flip's real
evidence is the data-layer measurement in the flip report, not this
corpus. Recording it because a check that passes for the wrong reason is
worse than one that fails.

**CBS club cells did not move at all.** Keyed by player rather than by
line index: **1,168 players, 0 club changes, 0 blanks.** The apparent
changes are row *permutations* — `NYM`→`ATL` paired with `ATL`→`NYM` —
i.e. drift re-sorted the rows under a stable club column. That is class
③, and it is byte-level confirmation of the design claim that CBS rows
are untouched by the flip.

### The six residual blank clubs are the ruled outcome

| player | roster days | days WITH a club | games |
|---|---|---|---|
| Joe Musgrove | 91 | **0** | **0** |
| Sebastian Walcott | 57 | **0** | **0** |

They never played. A player who never took the field has no club *of a
game*, so NULL is the honest answer — and a better one than the old
person-stamp, which asserted a current club for someone who never
appeared. This is MLB-193's ruling ("they stay NULL rather than
guessed") reaching the label surface, and it is 6 cells, not 441.

---

## 5. Gate

| gate | result |
|---|---|
| `dbt parse` | **clean** |
| `dbt build --target dev` (Snowflake, from the merged checkout) | **PASS=635, WARN=0, ERROR=0, SKIP=0**, 543 data tests, 3m43s |
| RAW refresh → DuckDB | 23 tables, 4.6M rows, 2,690.9 MB; `clubOfGame` now on **326 of 326** RAW rows (was 0 — the local copy predated the backfill) |
| DuckDB chain (`tools/duckdb_run.sh`) | **74/74 PASS, ERROR=0, SKIP=0**, clean in round 1, 20m26s. **No MLB-179 exit-139 flake** |
| **A/B parity, changed models** | **PASS** — `stg_box_scores` 194,946 rows and `fct_player_position_pts` 143,793 rows, **byte-identical fingerprints** on both engines |
| pure suite | **342 passed**, 2 failed — the two known WIP-file tests |
| byte-diff, both books, vs the kept anchor | **24 passed, exit 0**, with `REGENERATE_BASELINES` cleared |
| dev renders, both books | written to their dev sheets, no `--prod` anywhere |
| screenshots | **CAPTURED** — Kyle connected Chrome at the end of the session; both dev sheets walked live (see below) |

### The launch-screenshot state, verified in the live sheets

Every class that has a visible surface was confirmed on the dev sheets
themselves, not just in the TSV corpus:

| what | where | what it showed |
|---|---|---|
| **the fix** | ESPN `AAA` | Team column fully populated — Rutschman **Bal**, Soroka **Ari**, Crochet **Bos**, and every slot in both the current-season and all-time bands. This is the 441-blank regression, gone |
| ① tab order | ESPN `Home` | nav ends `... Draft Recap, Matchup History` |
| ① tab order | CBS `Home` | nav row 17 = `Season History / Every team-season since 2001, as awarded.`; the sheet list shows **Season History last**, after Salt Lake Bisons |
| ① the tab | CBS `Season History` | renders: title, *"Every finished season, 2001–2025, one row per team"*, the Outscored/Outscored By/Ties invariant stated in the subtitle, champion trophy marker, three-stop scales across both stat blocks |
| ④ affinity | ESPN `Advanced Standings` | **30 club rows ending at Washington Nationals (row 150), no Unattributed row**, both Current Season and All-Time bands populated |
| ⑥ Wasted tweak | ESPN `Records` | breakdown reordered *and* the font-size box reads **7**. Rows 118 and 124 are the proof the sort is by value rather than by a fixed order: `197 unrostered · 15 negative · 0 benched · 13 active` and `200 unrostered · 6 negative · 0 benched · 44 active` — `negative` ahead of `benched` where it is larger, `active` pinned fourth, percentage last |

The byte-diff verification is worth one extra note: run with
`REGENERATE_BASELINES` explicitly removed, so it *could* fail. Passing
means the corpus and the code agree, and incidentally that the render is
deterministic across two separate full runs.

### A/B parity — what the comparator normalizes, and why it is not a fudge

The first comparison run reported FAIL on both models. Both causes were
transport, not data, and both are worth naming because either could be
mistaken for a real divergence:

- the Snowflake connector returns Python `int` for `NUMBER(n,0)` where
  DuckDB returns `Decimal`;
- Snowflake serializes JSON pretty-printed, DuckDB compact.

Normalizing those two and nothing else gives identical fingerprints. The
tell that this is transport rather than data: **`PRO_TEAM` was never
among the differing columns in either model**, on either run — and
`pro_team` is the column the flip actually changed, so it is the one the
gate exists for.

---

## 6. What surprised me

1. **The merge was a fast-forward.** The conflict resolution the kickoff
   budgeted for did not exist, because 163 was built on the flip.
2. **The documented RAW-refresh command does not run as documented.**
   `py tools/dump_snowflake_raw_to_parquet.py` fails with
   `ModuleNotFoundError: pyarrow` — the `py` launcher resolves to system
   Pythons, and the repo `.venv` has no pyarrow either. The tooling lives
   in the **`mlb10-duckdb` venv** (pyarrow 25 / snowflake 4.4 / duckdb
   1.5.5), which is also where `duckdb_run.sh` already points `DBT_BIN`.
   Worth correcting in the flip report's §7 recipe.
3. **The RAW refresh took ~2 minutes**, not the heavy operation the "~2
   GB" warning implies.
4. **The defect was found by a process rule, not a test.** Every gate in
   this ceremony passed *with the defect in place* — `dbt build`, the
   pure suite, A/B parity, and the byte-diff would all have gone green
   against a corpus containing 441 blank club cells, because the corpus
   was regenerated from the same defective code. The only thing that
   caught it was the requirement to attribute every diffed cell to a
   declared class and stop on anything left over.

