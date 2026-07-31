# DuckDB Portability Audit (MLB-9)

**Date:** 2026-07-26. **Scope:** the `dbt_league/` transform layer only.
MLB-10's other halves -- extract landing (Snowflake RAW -> parquet/DuckDB)
and the output layer's connection swap (`output/db.py`) -- are noted where
they intersect but are not sized here.

**Spike setup:** dbt-duckdb 1.10.1 / DuckDB 1.5.5 / dbt-core 1.12.0 in an
isolated venv; a scratch copy of `dbt_league/`; one league + one season of
real data (espn-main 2025: 6 RAW tables, 195 scoring periods, ~46 MB of
box-score JSON) dumped from Snowflake to NDJSON and loaded into a
`.duckdb` file with VARIANT columns as `JSON`. The repo tree, the repo
venv, the dbt profile, and the Snowflake warehouse were untouched
(read-only SELECTs for the dump and reference rows).

---

## Verdict

**A focused week, not a weekend and not a month: ~5-8 working days** to a
green `dbt build` on DuckDB with values verified against Snowflake.

The evidence behind the number:

- Every break found falls into **eight mechanical classes** (table below).
  There is no construct in the project that DuckDB cannot express; nothing
  requires a redesign.
- The two structural classes -- `LATERAL FLATTEN` (26 sites / 15 files) and
  colon-path JSON access (~143 lines) -- were live-ported for the entire
  ESPN staging layer during the spike: **9 staging models rewritten in
  ~2 hours** including pattern discovery, and all of them built green
  *and passed their existing schema tests* on real data (252 passes,
  0 test failures). The per-file cost is 10-20 minutes now that the two
  rewrite templates exist.
- The heavyweights that looked scary are free: **QUALIFY (25 uses),
  MAX_BY/MIN_BY (17), MODE, the leaderboard UNPIVOT (standard form),
  quoted-part DATEDIFF, `::` casts, ILIKE, regex backrefs** all work in
  DuckDB unchanged.
- `stable_sum` (MLB-128) carries over intact -- see the float section; the
  one required change is a golden-neutral spelling fix done on Snowflake
  first.
- What keeps it from being a weekend: the unported CBS/MLB staging tail
  (12 files, same classes but denser, incl. one genuinely fiddly
  regex/timezone file), a values-verification pass the spike could not
  finish (disk incident, below), and a memory/materialization shaping pass
  that the spike proved is REQUIRED, not optional, on laptop-class
  hardware.

### Rough day map

| Phase | Work | Days |
|---|---|---|
| 0 | Pre-port on Snowflake (golden-neutral): `float`->`double` spellings (stable_sum + 24 `::float` sites + seed `column_types`), complete the `owner_nicknames` column_types, rename `do` aliases, wrap the one `FROM VALUES` | 0.5-1 |
| 1 | Dispatch macros for the 8 rename classes + the 2 JSON reshape templates (`iff`, `to_varchar`, json-path get, array-flatten, object-flatten, etc.) | 1 |
| 2 | Staging rewrite onto the macros: 9 ESPN files (done once already in scratch), 12 CBS/MLB files (`stg_cbs__ui_transactions` is the fiddly one: regexp_substr flags, convert_timezone, try_to_date format strings) | 1.5-2 |
| 3 | Mid/mart sweep: `iff` x72, `object_construct` x17, `to_varchar` x8, singles (`div0`, `mode` ok, `listagg`, `initcap`, `dateadd`, `to_timestamp_ntz`, array ops) + UNPIVOT stat_name case decision | 1 |
| 4 | Materialization + memory shaping: staging/int tables not views, DuckDB settings, re-test incremental facts (delete+insert) | 0.5-1 |
| 5 | Values verification vs Snowflake (the prepared A/B), golden re-anchor + diff review | 1-2 |

---

## Inventory: every Snowflake-ism found, with verdicts

Counts are from a full grep of `models/`, `tests/`, `macros/` on 2026-07-26.
"Battery" = verified live against DuckDB 1.5.5 this session; "Build" =
proven by the spike's `dbt build`.

### Works in DuckDB unchanged (no action)

| Construct | Sites | Evidence | BigQuery note |
|---|---|---|---|
| `QUALIFY` | 25 | Build | Supported |
| `MAX_BY` / `MIN_BY` | 17 | Build + battery | Supported (2023+) |
| `MODE(x)` | 1 | Battery | **Missing** -- needs `APPROX_TOP_COUNT` or a window rewrite |
| `DATEDIFF('day', a, b)` (quoted part) | 1 | Battery | Arg-order differs -> `dbt.datediff` |
| `SPLIT_PART` | several | Build | **Missing** -- `SPLIT()[SAFE_OFFSET()]` |
| `ILIKE` | ~10 | Battery | **Missing** -- `LOWER() LIKE` |
| `REGEXP_REPLACE` w/ `\\1` backrefs, `'g'`/`'i'` flags | ~8 | Battery | `\\1` -> `$1`? No: BQ uses `\\1` too; flags differ (`(?i)` inline) |
| `::type` cast syntax | 192 | Build | **Missing** -- `CAST()` everywhere (mechanical) |
| `cast(x as string)` | ~8 | Battery | STRING is BQ's native name |
| UNPIVOT (paren form, EXCLUDE NULLS default) | 1 | Battery | Supported; same default |
| `array_agg(distinct x)` + `order by` | 2 | Battery | Supported w/ syntax shim |
| dbt_utils tests (`unique_combination_of_columns`, `expression_is_true`) | ~60 | Build | Supported |
| Incremental `unique_key` configs (3 facts) | 3 | Parse | Supported (`merge`) |
| Seeds incl. emoji/unicode values | 17 | Build | Supported |
| `league_period_watermark` macro (correlated subquery) | 3 | Parse | Supported |
| `rate_stats.sql` macros (nullif + `* 1.0` division) | all | Parse | Supported |

**UNPIVOT caveat (Build-adjacent, battery-proven):** Snowflake emits the
folded `stat_name` as the stored UPPERCASE identifier (`'AB'`); DuckDB
emits the name as written in the query (lowercase `'ab'`). Everything
downstream keys on uppercase (`SEED_TO_LEADERBOARD`, dim_stat joins,
records.py). One-line fix (`upper(stat_name)` in the mart) or consumer
adjustment -- but it must be DECIDED, or the record book goes empty
silently.

### Mechanical rename (portable rewrite or 5-line dispatch macro)

| Construct | Sites / files | DuckDB replacement | BigQuery note |
|---|---|---|---|
| `IFF(c,a,b)` | **72 / 14** | `IF(c,a,b)` (identical semantics, battery) | `IF` native |
| `OBJECT_CONSTRUCT` | 17 / 1 | `json_object` | `JSON_OBJECT` |
| `TO_VARCHAR(x)` | 8 / 5 | `cast(x as varchar)` | `CAST(x AS STRING)` |
| `OBJECT_KEYS` | 3 / 1 | `json_keys` | `JSON_KEYS` (2024+) |
| `ARRAY_SIZE` / `ARRAY_REMOVE` / `ARRAY_CONSTRUCT` / `TO_VARIANT` | 10 / 2 | `len` / `list_filter` / `[..]` literal / drop | ARRAY_LENGTH / ARRAY ops |
| `LISTAGG ... WITHIN GROUP` | 2 / 1 | `string_agg(x, sep ORDER BY y)` (battery) | `STRING_AGG` |
| `INITCAP` | 2 / 1 | word-wise shim (battery-proven; exact Snowflake delimiter semantics differ -- verify names like "McAvery" via the preferred_name override path) | `INITCAP` native |
| `DIV0` | 1 | `coalesce(a / nullif(b,0), 0)` | same rewrite |
| `DATEADD(part, n, d)` | 2 | `d + INTERVAL n part` or `dbt.dateadd` | `DATE_ADD` |
| `TO_TIMESTAMP_NTZ(ms/1000)` | 1 | `epoch_ms(ms)` (keeps ms precision) | `TIMESTAMP_MILLIS` |
| `TO_TIMESTAMP_TZ` / `CONVERT_TIMEZONE` | 2 | `timezone(tz, ts)` -- **arg order + semantics differ; hand-check** | `DATETIME(ts, tz)` |
| `TRY_TO_DATE(x, fmt)` / `TRY_TO_NUMBER` | 3 | `try_strptime` (**format-string dialect differs**: `'MM/DD/YY'` -> `'%m/%d/%y'`) / `try_cast` | `SAFE.PARSE_DATE` |
| `TO_CHAR(d, fmt)` / `TO_NUMBER` | 2 | `strftime` + cast | `FORMAT_DATE` |
| `REGEXP_SUBSTR(s, pat, pos, occ, 'ie')` | 3 / 1 | `regexp_extract(s, pat, grp, 'i')` -- occurrence param doesn't exist (all our uses are occ=1, so it maps) | `REGEXP_EXTRACT` |
| `CHAR(160)` | 3 / 1 | `chr(160)` | `CHR` |
| `::NUMBER` | 1 | `::bigint` (bare NUMBER/DECIMAL defaults differ: SF (38,0), DuckDB (18,3)) | `NUMERIC` is (38,9) |
| `do` as table alias | 2 files | reserved word in DuckDB -> rename (build-proven parse error) | reserved too |
| `BOOLOR_AGG` | 1 (fct_roster_stints) | `bool_or` (build-proven) | `LOGICAL_OR` |
| `FROM VALUES ... column1..N` | 1 | `FROM (VALUES ...) v(c1,..)` (build-proven) | same rewrite |
| Seed `+column_types` partial coverage | 1 (owner_nicknames) | dbt-duckdb requires ALL columns typed or none (build-proven) | similar risk |

### Structural (the real port work)

| Construct | Sites | Rewrite |
|---|---|---|
| `LATERAL FLATTEN(input => arr)` over VARIANT arrays | 26 / 15 files | `, unnest(cast(x as json[])) as f(value)` -- NULL input yields 0 rows (matches flatten); **a JSON object (not array) errors instead of yielding NULL**, so the `coalesce(raw_json:matchups, raw_json)` legacy-shape guard is safe only while both arms are arrays |
| Flatten over VARIANT **objects** using `f.key`/`f.value` | 3 sites (roster_settings, breakdowns) | `, unnest(map_entries(cast(j as map(varchar, json)))) as f(kv)` then `(f.kv).key` -- handles keys containing `/` (`'K/9'`), which JSONPath extraction does not |
| Colon paths `x:a:b::type` | ~143 lines | `(x->'a'->>'b')::type`; keep `->` where the value stays JSON |
| VARIANT column typing in RAW | all staging | land as DuckDB `JSON` columns (spike did); parquet landing is MLB-10's call |

BigQuery: both patterns become `JSON_EXTRACT_ARRAY`/`UNNEST` +
`JSON_VALUE` -- same shape, third spelling. **Recommendation: put the
JSON reshape behind 3-4 adapter-dispatched macros** (`json_text`,
`json_sub`, `flatten_array`, `flatten_object`) so one staging tree serves
Snowflake + DuckDB now and BigQuery later. The alternative (per-engine
staging trees) doubles the review surface for zero payoff -- staging is a
pure reshape by project convention.

---

## stable_sum / DECIMAL / FLOAT (the MLB-128 question)

Tested directly (20-shuffle experiment on 10k values, plus casts):

- **DuckDB's DECIMAL sum is exactly as order-independent as Snowflake's.**
  `sum(cast(x as decimal(18,6)))` returned ONE distinct result across 20
  random input orders; a plain `sum(double)` over the same data returned
  16 distinct results. The MLB-128 mechanism transfers whole.
- Same widening (`DECIMAL(38,6)` accumulator), same empty->NULL, and
  **round-half-away-from-zero on both engines** for DECIMAL and DOUBLE,
  negatives included -- the almanac's rounding-boundary coin cells
  (382.75 / 443.05) round identically given identical inputs.
- **The one material difference: DuckDB `FLOAT` is 32-bit** (Snowflake's
  FLOAT is 64-bit). `cast(... as float)` narrows: `12345.6789` becomes
  `12345.6787109375`. This touches `stable_sum`'s final cast, 24 `::float`
  sites, and seed `column_types: float`.
  **Fix before the port, on Snowflake:** spell them `double` --
  FLOAT/DOUBLE are the same 64-bit type on Snowflake, so the change is
  golden-neutral there, and both engines then agree at 64-bit.
- Residual, unavoidable, bounded: DuckDB returns DOUBLE for `int/int` and
  `decimal/decimal` division where Snowflake keeps exact NUMBER with scale
  rules. Rate stats (ERA/WHIP/OPS...) are float-bound in both engines but
  may differ in terminal digits -> **display-rounded goldens re-anchor
  once at port time**, then hold (the sums themselves stay exact via
  stable_sum).

---

## What the spike actually ran

| Wave | State | Result |
|---|---|---|
| 1 | Unmodified project | **PASS 96 / ERROR 19 / SKIP 521.** All 19 errors in staging or seeds: 14 colon-path parse errors, 4 missing CBS/MLB source tables (those 4 models -- flat-column readers -- PARSE clean), 1 seed dialect break. Zero mid/mart errors surfaced (cascade skips). 2 seed-only models green. |
| 2 | + 9 ESPN staging ports, 5 CBS chain-neck stubs, seed fix | **PASS 252 / ERROR 14.** Every ported model green INCLUDING its schema tests. New errors were exactly the predicted next-layer classes: `iff`/`to_varchar` (int_player_daily), `do` alias (dim_owner), `FROM VALUES` (dim_roster_slot_counts). |
| 3-4 | + those fixes | dims + 3 marts green (incl. mart_period_standings, mart_player_career/season_records over stubs). |
| 4-6 | int_player_daily as TABLE | **OOM story below.** Halted before facts/marts/A-B. |

The CBS/MLB staging tail (12 files) was deliberately not ported --
inventoried above; same classes, one fiddly file.

## Performance and operational findings (laptop-class engine)

1. **Views over fat JSON are ruinous on DuckDB.** Prod materializes
   staging as views; every consumer (and every schema TEST) re-executes
   the full 46 MB JSON reshape. Wave-2 tests ran 25-80s EACH; after
   flipping staging/intermediates to tables, the same tests ran in
   0.04-0.05s. Snowflake's warehouse had been absorbing this cost
   invisibly. **The port must materialize the reshape layer as tables**
   (config-only change) -- also strictly better for the 2.0 run-anywhere
   story.
2. **int_player_daily CTAS OOM'd** (32 GB machine) when built over
   view-staging: the ~90-column pivot re-executed the JSON reshape ~4x
   inside one query; working set exceeded a 20 GB memory limit plus spill.
   With table-staging beneath it this is expected to be unremarkable, but
   that run was **halted by the disk incident and remains unverified** --
   first task of the follow-up. Relevant DuckDB knobs, verified:
   `preserve_insertion_order=false`, `memory_limit`,
   `temp_directory`, `max_temp_directory_size`.
3. **Machine-safety note (2026-07-26):** the OOM retries ballooned the
   Windows pagefile ~26 GB, taking C: from ~16 GB free to 1.7 GB.
   Builds were halted; a reboot reclaims the pagefile. A port dev-loop
   on consumer hardware should pin `memory_limit` modestly (4-8 GB) and
   cap `max_temp_directory_size` FIRST, not as a reaction.
4. dbt-duckdb differences worth knowing: seeds need complete
   `column_types` per file (partial dicts that Snowflake tolerates break
   the CSV sniffer); incremental default is `delete+insert` (fine for the
   whole-row unique_key pattern; runtime untested this session).

## Top 3 risks

*(Status update, same-day: the follow-up run largely retired #1 and #2 --
see "Follow-up results" below. #1 narrows to the full-history matched-
weights A/B + initcap spot-check in phase 5; #2 narrows to an
8 GB-class-machine diligence pass. #3 stands unchanged.)*

1. **Values-level equivalence is argued, not yet demonstrated end-to-end.**
   The A/B (DuckDB-built `fct_team_weekly_active_performance` +
   `mart_stat_leaderboard` vs 416 Snowflake reference rows + 1,218
   leaderboard rows, already dumped) was blocked by the disk incident.
   Float-division residuals, the UNPIVOT case difference, and the initcap
   shim could each move rendered cells; budget the phase-5 verification
   day and treat any unexplained diff as a stop.
2. **Memory/perf shaping on consumer hardware.** The OOM shows the wide
   single-pass models lean on warehouse elasticity. The mitigation list is
   known and config-level, but until int_player_daily builds green over
   table-staging on an 8 GB-class machine, the "runs on a stranger's
   laptop" promise (MLB-109/127) carries this open question.
3. **The CBS staging tail.** 12 unported files including
   `stg_cbs__ui_transactions` (regexp_substr flag semantics,
   convert_timezone, try_to_date format-string translation) and the
   identity chain -- same classes, densest instances, and the walk-back
   logic on top means value-checking those rewrites is slower than the
   ESPN side was.

## House dialect rules for the pre-port review

A hard review BEFORE the port mostly cannot shrink the port (the cost is
dialect surface, not code quality) -- but it can make the port a
zero-semantic-diff event, which is worth more. Rules discovered by this
spike, in review-checklist form:

1. **Never let a value-moving change and an engine-moving change share a
   commit.** Anything that changes rendered values (rounding, casing,
   tie-breaks, grain) lands under Snowflake first, re-anchoring goldens
   there; the port itself must then hold goldens byte-still.
2. **Write the portable intersection when equal-cost:** `case when` over
   `iff`; `cast()` names spelled `double`/`varchar`/`bigint` (never bare
   `float`/`number`); quoted datepart strings; no `do`/`values`/`group`
   as aliases (extend the existing Snowflake reserved-word convention to
   the union of engines); complete `column_types` on every seed.
3. **Dialect lives in macros, semantics in models.** rate_stats.sql is
   the house pattern; the JSON reshape belongs behind the same kind of
   seam. Review can pre-shape call sites; don't hand-rewrite flatten
   sites without the macros -- that's churn, not progress.
4. **No engine default may be load-bearing in DATA.** UNPIVOT's emitted
   name case, listagg ordering, initcap behavior, implicit sort orders:
   wherever column VALUES come from an engine mechanism, pin the
   convention explicitly in SQL (`upper()`, explicit `order by`).
5. **Materialization is a decision, not a default.** Warehouse elasticity
   hid the cost of view-staging over fat JSON; decide table vs view per
   layer deliberately (staging: table) -- cheaper on Snowflake too.

## Follow-up results (run same-day, 2026-07-26, after disk was freed)

The §Follow-up commands ran with safety knobs set FIRST
(memory_limit 6GB, max_temp_directory_size 6GB, pinned temp_directory,
preserve_insertion_order=false). Results:

1. **The memory question is closed: int_player_daily built as a TABLE in
   4.09s** over table-staging. The earlier OOM was entirely the
   views-under-CTAS orchestration artifact (the JSON reshape re-executing
   ~4x inside one query), not the model. Full 244-node chain: ~90s wall,
   198 passes, 0 test failures; observed spill peak ~1.7 GB, db file
   65->123 MB, system commit flat, no failures at the 6 GB cap. Risk #2
   below is RETIRED at desktop scale (an 8 GB-class-machine pass remains
   sensible diligence for 2.0's audience).
2. **Value A/B vs Snowflake prod (416 team-week rows, 8,320 cells;
   1,218 leaderboard rows):**
   - Counting stats and platform passthrough columns: **bit-identical,
     zero mismatches** -- the reshape/pivot/rollup machinery is
     value-perfect cross-engine.
   - Every `calculated_*`/`*_pts` mismatch decomposes to the
     **weights-vintage subset artifact**, proven numerically: prod applies
     current-season (2026) weights universally by design; the one-season
     sandbox resolves "current" to 2025. Team (9,3) has 47 K on BOTH
     engines: duck 58.75 = 47 x 1.25 (2025 weight), prod 47.0 = 47 x 1.0
     (2026 weight); OUTS likewise (1.0 vs 0.67). Not engine drift.
   - The **float32 trap appeared live exactly where predicted** (unfixed
     `as float` lineage): duck 553.5999755859375 vs prod 553.7. The
     phase-0 `double` spelling erases this class.
   - **UNPIVOT case confirmed at scale: 107/107 leaderboard stat_names
     differ only by case** (duck lowercase, prod UPPERCASE), zero missing.
   - Leaderboard all-time top-3: 836/1218 exact matches; every mismatch
     is the all-time-records-vs-one-season subset artifact (plus float32
     on point columns). Nothing unexplained.
3. **Incremental delete+insert parity: exact.** Second (non-full-refresh)
   run of fct_team_weekly_active_performance used dbt-duckdb's
   delete+insert (verified in compiled SQL), drove the per-league
   watermark (reprocessed period 26), and left checksums bit-identical:
   416 rows, calculated sum 205622.899712, platform sum 205558.500000,
   before == after. stable_sum determinism holds through the incremental
   path.
4. New finds folded into the tables above: `boolor_agg` -> `bool_or`;
   also note DuckDB propagates the CATALOG case of source columns through
   unaliased selects (mixed-case result metadata downstream of UPPERCASE
   raw tables) -- harmless to SQL (case-insensitive) but visible to
   Python consumers reading cursor descriptions.

Remaining before "port done" on the values front: the same A/B at full
history with matched weights (expected clean given the above), and the
initcap/name-formatting spot-check. Both live in phase 5 of the day map.

Artifacts (session scratchpad, `spk/`): construct battery + results,
stable_sum shuffle test, the 9 ported staging models + fixed dims, stub
generator, dump/load/compare scripts, wave + A/B logs, `raw_schema.json`,
`mem_samples.csv`.

## The laptop build profile (MLB-10, measured 2026-07-31)

How the full chain is actually built at the pinned 6 GB caps, and why it
takes more than one `dbt run`. This supersedes the earlier guess that the
run-level failures were a thread-count problem -- they are not, and no
`--threads` value moves them.

**Profile:** `--threads 1`, `memory_limit=6GB`,
`max_temp_directory_size=6GB` (5.59 GiB), `preserve_insertion_order=false`.

**Step 1 -- `dbt run`: 72 of 74.** Everything builds except
`mart_player_career_records` and `mart_player_season_records`, which fail
on the SPILL cap, not `memory_limit`: *"failed to offload data block of
size 256.0 KiB (5.5 GiB/5.5 GiB used) ... set by the
'max_temp_directory_size' setting."*

**Step 2 -- one further invocation PER failing mart.** Each then builds
and passes its own data tests:

| invocation | result |
|---|---|
| `dbt build -s mart_player_career_records` | model + 9 tests, 22.7s |
| `dbt build -s mart_player_season_records` | model + 7 tests, 19.2s |

Both are value-identical to Snowflake (5,460 and 4,620 cells, 0
mismatches), so 74/74 is reachable -- it just is not reachable in one
process.

**Why one extra invocation is not enough.** Putting BOTH marts in a
single second invocation fails exactly as the full run does: the first
builds, the second dies. They share one connection there, and that
adjacency is the whole problem. A fresh process resets the buffer pool
once, not between two statements inside it. Measured both ways.

**Why they are this expensive.** Each needs most of the entire spill
budget ALONE, at one thread:

| | peak spill | of the 5.59 GiB budget |
|---|---|---|
| `mart_player_career_records` | 3.42 GiB | 61% |
| `mart_player_season_records` | 4.14 GiB | 74% |

That leaves the worse one 1.45 GiB of margin, which anything else running
first consumes -- hence a failure that is state-dependent and looks
intermittent. Both read `int_cbs__player_season_stats`, whose
`crosswalked_games` aggregation over `stg_mlb__player_game`'s 42M rows
costs 3.37 GiB on its own.

**Do not "fix" this by materializing that view.** It was tried and is
worse -- 70/74, because the model then fails too and takes three
dependents with it. As a view the aggregation streams and the group-by
discards rows as it goes; as a table it must additionally retain all
890,902 output rows to write them, so the peak rises rather than falls.
A `count(*)` over a view is not a proxy for the cost of materializing it.

**Standing follow-up (quality, not a gate):** reshaping that 42M-row
aggregation is the only remaining lever on the two marts' peak. It is a
model change with value risk and wants its own reviewed ceremony; until
then the three-invocation profile above is the supported build.

### Headroom at cap (MLB-172) -- first data point, 2026-07-31

MLB-172 makes the observed PEAK per invocation a release-over-release
metric rather than a pass/fail. This is its first measurement.

**Instrument.** A sampler process walks DuckDB's spill directory
(`<dbfile>.tmp`) every 3s and records its total size, alongside the
working set of the dbt worker. Three things make the numbers trustworthy:
the spill dir holds at most 21 files and one walk costs 1.5ms against a
3,000ms interval, so the sampler is not itself a perturbation; the spill
dir settles to **0 bytes between invocations**, which makes per-invocation
attribution exact rather than cumulative; and the worker is found by
process size, because `dbt.exe` is only a launcher shim whose REAL worker
is a grandchild (`dbt.exe` 6 MB -> `python.exe` 6 MB -> `python.exe`
6 GB). Anything sampling `dbt.exe` reports ~10 MB and measures nothing.

**At the pinned 6 GB caps** -- the supported profile, `--threads 1`:

| invocation | result | wall | peak spill | of 5.59 GiB | headroom | peak worker RSS |
|---|---|---|---|---|---|---|
| `dbt run` | 72/74 | 613.8s | 4.54 GiB | 81% | 1.05 GiB | 6.76 GiB |
| `dbt build -s mart_player_career_records` | pass | 40.3s | 3.84 GiB | 69% | 1.75 GiB | 5.96 GiB |
| `dbt build -s mart_player_season_records` | pass | 36.2s | 4.58 GiB | 82% | 1.01 GiB | 6.06 GiB |

`dbt run` reproduced the documented `PASS=72 WARN=0 ERROR=2 SKIP=0`
exactly, and both bridged marts passed with their data tests.

Note these per-mart peaks read ~0.4 GiB above the 3.42 / 4.14 GiB recorded
for the same two marts higher up this section. The gap is consistent in
both direction and magnitude across both marts, which points at an
instrument difference -- a sampled directory high-water mark versus
whatever the earlier figure was read from -- rather than at any movement
in the models. **Trend future runs against this instrument's numbers, and
do not mix the two series.**

### The 8 GB-laptop diligence pass (2026-07-31)

Re-run of the same three invocations with `DBT_DUCKDB_MEMORY_LIMIT=4GB`
and the spill cap held at its 6 GB default. Tightening ONE cap is
deliberate: it keeps a failure attributable to a single knob. 4 GB is the
8 GB-machine end of operational finding #3's "modestly (4-8 GB)" range,
since an 8 GB laptop cannot hand 6 GB to one process alongside its OS.

| invocation | result | wall | peak spill | of 5.59 GiB | headroom |
|---|---|---|---|---|---|
| `dbt run` | **PASS=32 ERROR=4 SKIP=38** | 201.7s | 5.41 GiB | 97% | 0.18 GiB |
| `dbt build -s mart_player_career_records` | pass | 36.4s | 4.89 GiB | 87% | 0.70 GiB |
| `dbt build -s mart_player_season_records` | pass | 25.2s | 5.39 GiB | 96% | 0.20 GiB |

(Worker RSS is not reported for this run -- it was taken before the
launcher-shim problem above was found. Spill figures are unaffected,
being read off the filesystem rather than off a process handle. The
`dbt run` peak also understates a completing run, since that invocation
abandoned 38 models.)

**Tightening RAM raises spill.** Less memory means more of the same work
offloads to disk, so the two marts' peaks ROSE (3.84 -> 4.89 GiB and
4.58 -> 5.39 GiB) and headroom on the worst invocation collapsed from
1.01 GiB to 0.20 GiB. The two caps trade against each other; lowering one
pushes pressure onto the other.

**The failures split across BOTH caps, and only one class is bridgeable:**

| model | cap hit | own invocation? |
|---|---|---|
| `mart_player_career_records` | `max_temp_directory_size` (5.5/5.5 GiB) | **recovers** |
| `mart_player_season_records` | `max_temp_directory_size` (5.5/5.5 GiB) | **recovers** |
| `stg_cbs__rosters__teams` | `memory_limit` (3.7/3.7 GiB) | still fails |
| `stg_box_scores` | `memory_limit` (3.7/3.7 GiB) | still fails |

The two records marts behave exactly as documented -- they fail in
company and pass alone, so the three-invocation profile still carries
them at 4 GB. The two staging models are a different class: a fresh
process resets the buffer pool but not the ceiling, so no number of
invocations recovers a `memory_limit` failure. **74/74 is NOT reachable
at 4 GB**, and the 38 SKIPs are the downstream cone of those two.

**Measured floor**, isolated invocations of the two staging models:

| `memory_limit` | `stg_cbs__rosters__teams` | `stg_box_scores` |
|---|---|---|
| 4 GB | fail | fail |
| 5 GB | pass | fail |
| 5.25 GB | -- | fail |
| 5.5 GB | pass | pass |

`stg_box_scores` is the binding model, with its floor between 5.25 and
5.5 GB (single sample per boundary). **This is a headwind for MLB-109's
"runs on a stranger's machine" claim**: ~5.5 GB of `memory_limit` is
about 69% of an 8 GB machine's total RAM. Options are Kyle's call and are
drafted in the overnight handoff -- nothing here was reshaped.

## Sizing the output-layer connection swap (2026-07-31)

This document's header says the output layer's connection swap
(`output/db.py`) is "not sized here". Now it is.

**The connection itself is one file.** 131 `query_snowflake()` call sites
across 10 modules in `output/`, but `output/db.py` is the ONLY module in
the layer that imports `snowflake.connector`. Phase 7's consolidation
already made the connection a single choke point, so pointing the output
layer at DuckDB means adding a second backend behind the existing
`query_snowflake(sql, params) -> list[dict]` contract. **No call site
changes for the connection's sake.**

**The SQL dialect surface is small, and most of it is already free.**
Counted across `output/*.py`:

| construct | sites | on DuckDB |
|---|---|---|
| `QUALIFY` | 41 | free -- this document's known-good list |
| `MAX_BY` | 31 | free |
| `DATEDIFF('day', ...)` | 2 | free -- quoted-part form |
| `LISTAGG` (incl. `WITHIN GROUP`, `DISTINCT`) | 6 | rewrite to `string_agg` |
| `regexp_replace` | 6 | **the session-3 trap class** |
| `TO_VARCHAR` | 3 | shim |
| `try_to_number` | 2 | shim |
| `IFF(` | 2 | shim |
| `LATERAL FLATTEN` | 1 | rewrite; the template exists |

So ~20 sites of genuine divergence, every one in a class already solved
in `dbt_league/macros/dialect.sql`. The catch is that **none of that work
is reusable here**: output-layer SQL is Python string literals with no
Jinja, so the macros cannot dispatch and `re_literal` cannot guard
anything. The fixes are known; the delivery vehicle is not shared. That,
not the count, is the cost driver.

The six `regexp_replace` sites deserve naming, because session 3 lost a
day to exactly this class: `cbs_almanac_sheets.py:413-416` compose a name
key and `:2329` / `:2370` strip a parenthetical. The engines disagree on
backslash escaping AND on how many occurrences are replaced without a
`'g'` flag, and in dbt that combination produced a silent no-op that
fragmented the whole CBS identity spine.

**The driver contract was measured, not assumed** (read-only probe
against the built DuckDB file):

- **JSON/VARIANT columns come back as `str`, not native objects**, and
  `json.loads()` parses them. This is the same shape
  `snowflake-connector` produces, so `formatters.py:90`'s documented
  expectation and `generate_summary.py`'s defensive branch both hold
  unchanged. This was the risk that looked biggest and it is a non-issue.
- **Column case is NOT uniformly lowercase.** `mart_player_career_records`
  returns `LEAGUE_KEY` uppercase beside lowercase siblings, and a plain
  `select 1 as Mixed_Case_Col` preserves case. `db.py` already lowercases
  `desc[0]`, so this is handled -- but that lowercasing is load-bearing on
  DuckDB too, not a Snowflake-only affordance. Do not "simplify" it away.
- **Numerics come back mixed**: `Decimal` for `first_season` /
  `last_season`, `float` for `stat_value`, `int` for `rank` /
  `seasons_played`. Snowflake's connector applies its own rule, so this is
  the one contract difference with real potential to move a rendered cell,
  and it is where the verification pass should aim. MLB-128's float
  determinism work makes it worth taking seriously.

**Estimate: 1.5-2.5 days.** Roughly half a day for the db.py backend plus
type normalization, half a day for the ~20 dialect sites, and a day for a
render-level A/B proving no cell moved -- the part that cannot be skipped,
because every remaining contract risk surfaces as a formatting difference
rather than as an error.
