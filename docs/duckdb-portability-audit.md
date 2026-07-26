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
