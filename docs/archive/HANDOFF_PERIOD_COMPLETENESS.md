# Handoff -- period completeness, and the week-20 session that produced it

**Session:** 2026-08-24, Claude Code on the main checkout, `main`.
**Origin:** the Week 20 weekly update. It did not finish as a weekly update;
it turned into a design session, which is the more valuable outcome and the
reason this document exists.

**Companion documents:**
- `docs/matchup-period-lifecycle-DRAFT.md` -- the design direction (proposal,
  not in force).
- `Study Material.MD` -- two `08-24` curveball rows and three question-log
  lines (private, gitignored).

---

## 1. THE LANDING SPOT -- read this first

Kyle's words, and the thing everything else in this document is subordinate to:

> **"We should be able to run all available data and determine what timeframes
> should and should not be considered complete."**

Unpacked, because each clause is load-bearing:

**"run all available data"** -- ingestion is unconditional. Pull whatever the
platform will serve, whenever, in whatever state. There is no gate, no
override, no operator judgement call. The newest fetch of an unfinished
timeframe is simply the best currently-available answer about an unfinished
timeframe.

**"determine"** -- and this is the inversion. **Completeness becomes a property
WE derive, not a flag we defer to.** Today the pipeline outsources the
completeness judgement to the platform's `currentMatchupPeriod`, which means
the platform's lag becomes our outage. Under the landing spot, that field
demotes to *one input among several* -- alongside the game schedule, whether
the underlying MLB games are final, and the correction lag (§4) -- and stops
being the authority.

**"what timeframes"** -- deliberately broader than "matchup periods". Days,
weeks, periods, seasons. Completeness is evaluated at whatever grain a claim
is being made at (§5).

**"should and should not be considered complete"** -- the output is a
*classification*, published and readable, that every downstream consumer
consults. Not a filter applied once at the edge.

Everything below is either the evidence that got us here, or the work list.

---

## 2. What actually ran tonight, in order

Fuller state in §7. This is the narrative, because the corrections matter more
than the outcomes.

| # | Step | Result |
|---|---|---|
| 1 | CBS token probe | **HTTP 401 x10**, then clean on retry -- see §3 |
| 2 | CBS capture, forced, 08-17 -> 08-23 | `verdict: PASS`; period_22 standings landed |
| 3 | `cbs_load --force` | 22 standings / 152 roster / 11 txn / 55 config rows; committed |
| 4 | ESPN `extract.py --year 2026 20` | **REFUSED** -- see §3 |
| 5 | `mlb_crosswalk` | 2,165 / 2,167 mapped (99.9%), 0 collisions |
| 5 | `mlb_stats --discover 2026` | 19 missing, 17 files landed |
| 5 | `mlb_stats --min-season 2026 --force` | 3,843 players, 1,329 gamelog-seasons, **5,172 calls** |
| 5 | `mlb_load --force` | in flight at handoff -- see §6 |
| 6-8 | build / pytest / dev renders / recap | **not reached** |

No commits. Nothing pushed. No goldens touched. No `--prod` anywhere.

---

## 3. The corrections, in the order they happened

This section is the substance. Four claims were made and overturned tonight,
three of them mine.

### 3a. "The CBS token is dead" -- wrong, and it nearly sent Kyle to re-mint

The probe returned HTTP 401 on all 10 endpoints. Nothing landed, so this was
not a 200-but-empty ambiguity, and the token was 7 days old against a
documented 2-11 day TTL. I stopped the run and reported it dead per the
kickoff's instruction.

Kyle said "try once more." The identical command then returned HTTP 200 on 9 of
10 (the 10th, `transactions_alt`, is a known 404) with 16 teams and 480 roster
slots -- verified by content, not status.

**Lesson: a uniform 401 sweep is not proof of expiry.** Every endpoint failing
identically is as consistent with a transient auth-service blip as with a dead
token, and the two have very different costs -- one is a retry, the other is a
manual re-mint that blocks the run. Retry before declaring a credential dead.
The probe is cheap and read-only; there is no reason not to.

### 3b. "The positional period bypasses a different gate" -- wrong

The kickoff asserted, confidently: *"a positional period is the designed escape
hatch with no completeness gate."* The extract refused. I reported the stop and
reasoned that the positional form must bypass a different gate than the one
that fired -- which I had read out of the **error text**, not the code.

The code says the opposite. `select_periods` documents three spellings and
checks every one against the same `parse.closed_periods`:

- `--all` -> `list(parse.closed_periods)`
- explicit ids -> `offenders = [mp for mp in requested if mp not in closed]` -> refuses
- default -> the recent subset of closed periods

Its docstring is explicit: *"explicit ids -- checked against the closed set."*
The positional form's only distinction is that it refuses **loudly by name**
rather than silently narrowing. **There is no escape hatch.** The premise was
false in the brief before it was false in my summary.

**Lesson: when a brief hands you both the mechanism and the override, the brief
is a hypothesis.** A refusal message describes the branch that fired, never the
set of branches that exist.

### 3c. "Something differentiates this from closed-but-lagging" -- wrong; Kyle was right

Kyle pushed back: he believed period 20 *was* exactly a closed-but-lagging
period. Measuring instead of inferring -- the refused run had already written
its `mMatchupScore` snapshot to `RAW.MATCHUP_SCHEDULE` before the gate fired --
flattening `pointsByScoringPeriod` gave:

| matchup period | sides | keys | scoring periods | dates |
|---|---|---|---|---|
| 17 | 14 | 7 | 125 -> 131 | |
| 18 | 14 | 7 | 132 -> 138 | |
| 19 | 14 | 7 | 139 -> 145 | thru 08-16 |
| **20** | **14** | **7** | **146 -> 152** | **08-17 -> 08-23** |
| 21 | 14 | 0 | -- | unplayed |

Period 20 is structurally indistinguishable from the three closed weeks before
it: full 7-day key set, all 14 sides agreeing, spanning exactly 08-17 -> 08-23
off the 2026-03-25 scoring-period-1 anchor. Status block: `currentMatchupPeriod`
20, `latestScoringPeriod` 152, `finalScoringPeriod` 187, `currentLeagueType` 0.

The specific failure the guard exists to prevent -- the docstring's measured
*"2026's period 19 read 2 scoring periods against the seed's 7"* -- is
**demonstrably not happening**. The guard fires on a **proxy**
(`matchupPeriodId < currentMatchupPeriod`) rather than on the property it cares
about (completeness), and the proxy was wrong.

**Adjacent near-miss:** `EXTRACTED_AT` on my own fresh snapshot read
`2026-08-23 21:39`, which looks exactly like "the write never happened / the row
is stale." It is Snowflake's **America/Los_Angeles session timezone** rendered
NTZ against an Eastern machine -- a 7-hour offset, caught only by pricing it
against the CBS verify's explicit `...Z` stamp. Machine local was
`2026-08-24 01:08 -04:00`.

### 3d. "Not reprocessing everything since 2001" -- wrong; Kyle was right again

Kyle: *"there has got to be a better way to run a weekly refresh than
reprocessing all mlb data since 2001."* I corrected him: `--min-season` gates
the gamelog loop, so the weekly is not re-pulling since 2001.

True of the **fetch**. False of the **load**, one line later.
`mlb_load --force` runs `TRUNCATE TABLE {table}` with **no season predicate**
and sets `skip_cache = set()` ([mlb_load.py:225](../../extract/mlb_load.py)),
so it wipes the whole gamelogs family and reloads every file on disk,
2001-2026. The step really does reprocess all MLB data since 2001.

Caught by querying the table mid-run rather than by re-reading the flag:
`RAW.MLB_GAMELOGS` sat at **0 rows across 0 distinct seasons** while the reload
was in flight.

**Lesson: his blunt grain was the right grain.** My precise answer was scoped
to the command I had read; his framing was scoped to the step he actually runs.
When those disagree, the user's grain usually wins.

**Operational hazard this exposed, worth its own note:** `--force` on a
TRUNCATE+reload loader makes the table transiently **empty**, not transiently
stale. Anything that starts mid-window reads zero -- and zero is the value that
looks like a successful build. A `dbt build` in that window would have produced
a confidently blank almanac.

---

## 4. Why "looks finished" is not "is finished" -- the correction lag

Kyle, and this is what killed my proposed promotion-on-shape rule:

> *"If Shohei (or, presumably, any other two-way player) both pitched and hit
> today, it wouldn't be until the morning (specifically the period's close)
> that those had been accounted for. So I think the logic ought to really be
> 'shape of a closed period, plus the period before.'"*

Score corrections land at the close rather than as days accrue. Two-way players
are the clearest case because pitching and hitting lines reconcile separately,
but they are an instance, not the class. Independent local evidence that this is
real: the week-12 WALK check settled to the **calculated** value, and the
platform's own number is still wrong (logged in BRAINTHOUGHTS, unused).

Two structural facts compound it:

- **The parser reads KEYS ONLY** -- `_side_membership` is explicit that values
  are never read. A complete key set proves *shape* and is structurally silent
  about whether the *points* are final.
- **`latestScoringPeriod` is not proof of settlement.** On 08-24 it was 152,
  which *is* period 20's last day, and the parser's own docstring warns why
  that is not enough: *"the final scoring day can be the day in progress."*

Kyle's "plus the period before" is recorded **verbatim** and deliberately not
reinterpreted -- it needs a precise definition before it becomes code. The
reading I take is a one-unit lag: complete, *and* something subsequent
establishes the correction window closed. Periods vs scoring-period days vs an
observed-stable re-fetch are three different rules; that is open question #1.

---

## 5. The design, as it stands after Kyle's correction

My first draft of the design still gated ingestion (a "state floor" in
`select_periods`), which contradicted the landing spot. Kyle corrected it:

> *"There is no reason we need to wait for a matchup to finish to import its
> results SO LONG AS we know not to count its results as though it was a
> finished matchup. Don't say we have a new record for fewest home runs on a
> Tuesday; don't say that a team ahead 6 points to 4 at 1pm on Monday won a
> game."*

**Diagnosis: the closed-period restriction is at the wrong LAYER, not merely
too coarse.** It protects an *aggregation* invariant but is enforced at
*ingestion*, which is why it presents as a gate to be defeated.

**Two refinements those examples produced:**

1. **Completeness is per-grain.** "Fewest home runs on a Tuesday" is a *daily*
   claim; "who won the matchup" is a *period* claim. The grain of the
   completeness test must match the grain of the claim -- a finished Tuesday
   inside an unfinished week can support a daily record but nothing weekly. A
   period-level flag alone cannot express that.
2. **Extrema are asymmetric, which makes records the nastiest consumer.** A
   partial unit is structurally a minimum for any counting stat, so it
   spuriously wins every "fewest/lowest" record and can never win a
   "most/highest" one. The bug is dominant in half the record book and
   invisible in the other half.

**The original guard is evidence FOR this redesign.** What a short period
actually broke was `derive_period_shape`'s **mode** over scoring-period counts:
it corrupts the league's standard length, which then mislabels *real* weeks as
abnormal. That is a defect in a statistic three layers downstream of the
extract. The right fix for "a partial period corrupts a mode" is to exclude it
*from the mode*, not from the warehouse.

The full inventory of derivations that presume completeness (outcomes, extrema,
standard period length, standings, rates, streaks, goldens, almanac facts) is
§5 of the design draft. **Anything missed off that list becomes a silently wrong
number.**

**Safety property that makes it shippable:** an incomplete unit can never move
a golden or set a record. Assert it with a test observed to fail first (the
08-05 lesson: a guard never seen to fire is an assumption).

**Do not** admit period 20 today by patching the guard. The marts have no
completeness column yet, so it would land indistinguishable from a final week
and flow into records and the period-length mode. That is the same family as
`pro_team` describing extract-time (MLB-159/168) and the affinity chart's `FA`
band replacing a silent error with a stated falsehood. The row must say what it
is -- which is why the state is a column, not a flag on a run.

---

## 6. State at handoff

**Warehouse (measured):**

| | Before | Now |
|---|---|---|
| ESPN `MART_TEAM_MATCHUP` 2026 | 19 | **19** (box scores for 20 never fetched) |
| ESPN standings / schedule / calendar | -- | refreshed; landed before the gate fired |
| CBS `RAW.CBS_STANDINGS` 2026 | 21 periods | **22 periods** |
| CBS `MART_PERIOD_STANDINGS` 2026 | 21 | 21 (awaiting `dbt build`) |
| `RAW.MLB_GAMELOGS` | 08-16 | **2026-08-23** (1,818,901 rows / 43 seasons) |

`mlb_load --force` completed: 4 families, ~3.87M rows, 82,289 files --
`MLB_SEASON_STATS` 44,359 · `MLB_GAMELOGS` 1,818,901 · `MLB_FIELDING` 55,692 ·
`MLB_GAME_POSITIONS` 1,951,819. **The reload reaches back to 1984, not 2001**,
so §3d's scope is wider than Kyle's phrasing even allowed for.

**Repo:** `docs/matchup-period-lifecycle-DRAFT.md` (new),
`docs/archive/HANDOFF_PERIOD_COMPLETENESS.md` (this file, new),
`docs/decisions/README.md` (index entry), `Study Material.MD` (untracked).
`docs/index.html` and `output/imagegen/` were already dirty at session start
and are not from this session.

**The run completed through the dev renders.** Results:

- `dbt build` -- **PASS=836 WARN=1 ERROR=0 SKIP=0 NO-OP=4 TOTAL=841**, 7m00s.
  The one warning is `assert_live_season_has_schedule_capture` on `cbs-bsb`
  2026 -- structural, pre-existing, WARN-by-design, and discussed in the
  design draft as the same gap costing us elsewhere.
- `pytest` -- **1803 passed, 2 failed, 20 skipped, 27 deselected.** Both
  failures are in `tests/test_records_report.py`, the untracked WIP CLAUDE.md
  designates as ignorable. No regressions.
- ESPN dev sheet -- `1J07wsX8rtX0owoV4foS2QUyH31TiuvHGES47x3uEdow`, rendered
  **`for 2026 MP19`**: 22 weekly + 22 season-to-date all-league rows, 124
  records rows, 682 team-week rows, 14 roster tabs. (All-league sections read
  22 because the union layer picks up CBS's period count; not an ESPN
  inconsistency.) Three Sheets quota hits, each recovered on a 70s backoff.
- CBS dev sheet -- `1itf9U4Wbi_4xEaSkHYR1Mo-TKcFEChs0EmZ01LRKwGw`, 21 tabs.
  One quota hit, recovered.
- Recap -- generated **week 19**, not 20, exactly as the mart state predicted.
  Written to `scratchpad/week19-recap-espn_REGENERATED-2026-08-24.txt` rather
  than the kickoff's `week20-recap-espn.txt`: filing a week-19 recap under a
  week-20 name is the same stated-falsehood class as the `FA` band. CBS has
  `sinks.bbcode: false`, so no CBS recap -- config truth, not a bug.

### Follow-up run, 2026-08-24 later the same day

ESPN closed the period and the re-run completed it. `currentMatchupPeriod` 20
-> **21**, `latestScoringPeriod` 152 -> **153**, 20 closed periods. Period 20
extracted cleanly and `RAW.BOX_SCORES` stored **7 scoring periods, 146-152** --
structurally identical to period 19 (139-145) and exactly the membership read
out of `pointsByScoringPeriod` the night before, while it was still refused.
**The snapshot measurement predicted the stored result precisely**, which is
the evidence that deriving completeness from the payload is tractable.

**And it eliminated a design candidate.** ESPN advanced the scoring frontier
and the close flag *together* -- 152/current-20 to 153/current-21 in one step.
So the proposed "latestScoringPeriod advanced past the unit's end" proof would
have fired at the same instant as the close flag and bought **zero lead time**;
it restates the platform's flag rather than independently proving anything. The
evidence with real lead time is one we already hold and do not consult:
`RAW.MLB_GAMELOGS` knows when the week's games went final, hours earlier, from
the MLB Stats API. See open question 1 in the design draft, now amended with
this measurement.

**Follow-up run results.** `dbt build` PASS=836 WARN=1 ERROR=0 SKIP=0 (7m11s,
same pre-existing `cbs-bsb` warning). `pytest` 1822 passed / 3 failed -- the
three documented-expected local failures (`test_records_report.py` x2, the
untracked WIP; `test_demo_isolation.py::test_typed_override_is_honoured`, the
real-warehouse-on-disk case). Marts: ESPN **20**, CBS **22**, MLB max
`game_date` **2026-08-23** -- all three targets met. ESPN dev sheet re-rendered
`for 2026 MP20` (696 team-week rows, up from 682: exactly +14, one per team).
CBS dev sheet re-rendered, 21 tabs. Recap regenerated as **Week 20** to
`scratchpad/week20-recap-espn.txt`.

**Published to prod on Kyle's call** (the kickoff had said dev-only; he
overrode after eyeballing dev). ESPN prod `1C_CJ-jAZ-...` and CBS prod
`1l7fi-DH4Srm...`, both `for 2026 MP20` / 21 tabs respectively.

**One prod-only failure, and it is worth a ticket beyond the manual fix:**

    [almanac] trades formatting skipped: APIError: [400]:
    Invalid requests[86].mergeCells: You can't merge cells that cross
    the borders of an existing filter.

Neither dev render hit this, so it is a property of the prod workbook's
current state (an existing filter on the Trades tab), not a code regression
from this run. The data was written; the formatting pass bailed. Two things
follow. First, clearing the filter and re-rendering should fix it durably.
Second, and the reason it deserves a ticket: **a formatting pass that fails
partway leaves the tab half-styled, and the only evidence is one log line on
a run that still exits 0.** That is the same shape as the MLB-147 note that
formatting never reaches the TSV goldens -- the byte-diff structurally cannot
see it, so a silent half-format on the PUBLISHED book is invisible to every
automated gate we have.

**One more ambient-inputs data point, same family as the 08-12 curveball rows.**
The pytest failure set differed between the two runs *for environmental reasons
only*: launched from PowerShell the `demo.sh` tests SKIPPED (20 skipped, "demo.sh
needs bash"); launched via the Bash tool, Git Bash was on PATH so they RAN, and
one failed for the documented reason. Same suite, same commit, different result
set. **The shell that launches the suite is an undeclared fixture.** Related
near-miss in the same run: piping pytest through `| tail` made the pipeline
report `tail`'s exit 0 and masked pytest's 1 -- the wrapper's idea of "done"
needing its own evidence, again.

---

## 7. The separate thread: the instructions were imperfect

Kyle flagged this explicitly and it should not get lost inside the design work.

The kickoff was confident and wrong in one specific way -- it asserted a
mechanism (*"positional period ... no completeness gate"*) and an
override that do not exist, and it presented them as settled fact rather than
as something to verify. I then relayed that premise forward instead of reading
`select_periods`, which cost a round trip and would have cost more if Kyle had
not pushed back.

Two process changes worth considering, neither of which is mine to decide:

- **A kickoff that names a specific flag or code path should cite it**, so the
  first thing the session does is confirm it rather than assume it. A brief
  that says "positional period is the escape hatch" and a brief that says
  "positional period is the escape hatch (`select_periods`, extract.py:3197)"
  produce very different first moves.
- **The weekly recipe should stop carrying the override at all.** It has now
  been re-litigated in several consecutive sessions, which is the strongest
  possible argument that the recipe is encoding a workaround rather than a
  procedure. Once the landing spot lands, step 4 becomes unconditional and the
  whole paragraph disappears.

Also: the recipe's step 5 should be re-scoped once the `mlb_stats` /
`mlb_load` ticket is done. It currently reads as "surgical weekly" and is
5,172 API calls plus a 25-season truncate-and-reload.

---

## 8. Open questions -- for Kyle, deliberately not guessed

1. **What proves the correction window elapsed?** Next unit exists/closed
   (Kyle's "period before"); `latestScoringPeriod` advanced N days past the
   unit's end; or a re-fetch producing byte-identical values twice. The third
   measures settlement *directly* rather than by proxy -- which is the entire
   complaint -- but costs a second fetch and needs a stability window.
2. **How many states, at which grains?** Minimum viable is probably a boolean
   at day grain plus a three-way at period grain (incomplete /
   complete-unsettled / final). States are easy to add and hard to remove.
3. **Does incomplete data enter the marts** with a completeness column, or land
   in a parallel provisional surface? Former is far more useful and touches
   every fact; latter is cheap and safe. Main scope call.
4. **Re-extraction vs the settled-history guard.** Unconditional ingestion
   means an unfinished period is re-pulled and overwritten repeatedly --
   correct while unfinished, forbidden once final, since MLB-188 exists to stop
   a thinner re-fetch replacing stored `clubOfGame` and aged-out free agents.
   The completeness column is what lets those coexist, and getting the handover
   moment wrong is the most likely way to lose real history.
5. **Do CBS timeframes share the vocabulary?** CBS runs +2 on ESPN's numbering
   with different close semantics. One shared vocabulary with per-platform
   evidence is the MLB-81 / MLB-64 pattern, but it needs saying.
6. **What does the recap say** when its week is incomplete? Wording is product
   and it is load-bearing.
