# Incomplete periods -- ingest freely, derive carefully (DRAFT)

**Status: PROPOSAL. Nothing has moved.** Filed in `docs/` rather than
`docs/decisions/` for the reason that directory's README gives: it holds
documents still in force, and this one records a direction, not a decision.

**Raised by Kyle, 2026-08-24**, during the week-20 weekly update:

> *"Restricting around closed periods is a shitty sloppy workaround that
> vaguely approximates what we actually want. We absolutely must stop
> quibbling over how to define the stopgap and start defining how to close
> the gap. We need a way to do this that accepts unfinished periods and
> treats them as exactly that -- something different from finished periods."*

And then, sharpening it to the load-bearing sentence:

> *"There is no reason we need to wait for a matchup to finish to import its
> results SO LONG AS we know not to count its results as though it was a
> finished matchup. Don't say we have a new record for fewest home runs on a
> Tuesday; don't say that a team ahead 6 points to 4 at 1pm on Monday won a
> game. We can and should take in incomplete matchup data, we should just
> recognize that it is incomplete."*

This document exists because that instruction is correct and because several
consecutive weekly sessions have re-litigated the stopgap instead.
**Carry this into the handoff.**

---

## 1. The one-sentence diagnosis

**The closed-period restriction is at the wrong LAYER, not merely too coarse.**

The invariant it protects is an *aggregation* invariant -- do not let a partial
week participate in a claim that presumes a whole week. It is enforced at
*ingestion*, in `select_periods`, which is why it presents to an operator as a
gate to be defeated rather than as a rule about arithmetic. Every stopgap
proposed so far (force flags, positional periods, my own promotion-on-shape
rule) is an attempt to defeat that gate. None of them is wrong about wanting
the data; all of them are arguing at the wrong layer.

Move the discipline to where the invariant actually lives and the gate has
nothing left to do.

---

## 2. The two rules

Kyle's own statement of the target, and the sentence to design against:

> **"We should be able to run all available data and determine what timeframes
> should and should not be considered complete."**

Note what "determine" does there. **Completeness becomes a property we DERIVE,
not a flag we defer to.** Today the pipeline outsources that judgement to
`currentMatchupPeriod`, which is why the platform's lag becomes our outage.
Under this design that field demotes to one input among several -- alongside
the game schedule, whether the underlying MLB games are final, and the
correction lag of §4 -- and stops being the authority. "Timeframes" is likewise
deliberately broader than "matchup periods": days, weeks, periods, seasons.

**Rule 1 -- ingestion is unconditional.** Pull the period whatever state it is
in. No closed-set test, no override, no operator judgement call on a Monday.
The newest fetch of an unfinished period is simply the best answer currently
available about an unfinished period.

**Rule 2 -- every derivation that presumes completeness must scope itself to
complete inputs.** This is where all the actual work is, and §5 is the
inventory.

The recurring cost today is not the delay. It is that the operator makes a
judgement call every week and re-derives it from an error message each time.
Rule 1 deletes that call. Rule 2 is what makes deleting it safe.

---

## 3. What the guard was right about -- do not delete the invariant

Worth stating plainly so nobody "fixes" this by removing the protection along
with the gate.

`parse_matchup_membership`'s docstring records the measured reason a period in
flight is dangerous: it reads SHORT, and a short in-flight period is
indistinguishable from a real abnormality. The measured case is 2026's period
19 reading 2 scoring periods against the seed's 7.

Note *what* that breaks: `derive_period_shape` takes the **mode** of
`scoring_period_count` across closed periods to establish the league's standard
length, then flags every period differing from it as abnormal. Feed it a
partial period and it corrupts the norm, which then mislabels real weeks. That
is an aggregation defect, in a statistic, three layers downstream of the
extract.

**So the original guard is evidence for this redesign, not against it.** The
right fix for "a partial period corrupts a mode" is to exclude partial periods
*from the mode*, not to refuse them from the warehouse.

The other thing to preserve: a `--force` that admits provisional data as
final is the wrong shape, because it makes provisional and final
indistinguishable once landed. This repo has paid for that lesson three times
-- `pro_team` describing extract-time while its name implied it described the
row (MLB-159/168, 476 of 897 players blank), the affinity chart's `FA` band
replacing a silent error with a stated falsehood, and the MLB-188 guard whose
documented rationale named a dead field. **The row must say what it is.** That
is the whole reason the state is a column rather than a flag on a run.

---

## 4. Completeness is per-grain, and that is what makes it useful

Kyle's two examples are at *different grains*, and the difference is the
design's sharpest tool.

- *"fewest home runs on a Tuesday"* -- a **daily** claim. Its completeness
  test is whether that Tuesday's games are done, not whether the week is.
- *"a team ahead 6 to 4 at 1pm on Monday won a game"* -- a **period** claim.
  Its completeness test is whether the matchup is over.

So the rule generalises: **the grain of the completeness test must match the
grain of the claim.** A finished Tuesday sitting inside an unfinished week can
legitimately support a daily record while being unable to support anything
weekly. A period-level flag alone cannot express that, and a design that only
carries one would either block good daily claims or admit bad ones.

This also means the useful attribute is not really "is this period closed" but
"is the *unit this number describes* finished".

### The correction lag

Kyle, same session, on why finished-looking is not finished:

> *"If Shohei (or, presumably, any other two-way player) both pitched and hit
> today, it wouldn't be until the morning (specifically the period's close)
> that those had been accounted for. So I think the logic ought to really be
> 'shape of a closed period, plus the period before.'"*

Score corrections land at the close rather than as days accrue. Two-way players
are the clearest case because pitching and hitting lines reconcile separately,
but they are an instance, not the class. Independent evidence that this is real:
the week-12 WALK check settled to the *calculated* value, and the platform's own
number is still wrong (logged in BRAINTHOUGHTS, unused).

Two structural facts compound it, and together they kill any promotion rule
based on membership shape -- including the one I proposed and have withdrawn:

- **The parser reads KEYS ONLY.** `_side_membership` is explicit that values
  are never read. A complete key set proves *shape* and is structurally silent
  about whether the *points* are final.
- **`latestScoringPeriod` is not proof of settlement.** On 08-24 it was 152,
  which *is* period 20's last day, and
  `parse_matchup_membership`'s docstring warns exactly why that is not enough:
  "the final scoring day can be the day in progress."

Kyle's "plus the period before" is recorded verbatim because it needs a precise
definition before it becomes code and I do not want to silently reinterpret it.
The reading I take is a **one-unit lag**: complete, *and* something subsequent
establishes that the correction window closed. Whether the lag is counted in
periods, in scoring-period days, or by an observed-stable re-fetch is §7.

---

## 5. The inventory -- derivations that presume completeness

This is the real work list. Each of these currently gets its safety for free
from the ingestion gate, and each needs its own scoping once Rule 1 lands.
**Anything on this list that is missed becomes a silently wrong number.**

| Derivation | Why it breaks on a partial unit | Direction of the error |
|---|---|---|
| **Outcome (W/L/T)** | An unfinished matchup has no winner. | Fabricates a result from a mid-period lead. Kyle's 6-to-4-at-1pm case. |
| **Extrema / records** ("fewest X") | A partial unit is structurally a minimum for any counting stat. | **Asymmetric and nasty**: partial units spuriously WIN "fewest/lowest" records and can never win "most/highest" ones -- so the bug is invisible in half the record book and dominant in the other half. Kyle's Tuesday-home-runs case. |
| **Standard period length** (`derive_period_shape`) | Mode over `scoring_period_count`; a short period corrupts the norm. | Then mislabels *real* weeks as abnormal. The measured original defect (§3). |
| **Standings / cumulative W-L** | Sums outcomes that do not exist yet. | Advances a record on a game nobody won. |
| **Averages and rates** | Denominator is games/days/periods elapsed. | Silently deflates or inflates depending on which side is partial. |
| **Streaks and runs** | A partial period reads as a loss or a win depending on the moment. | Breaks or extends streaks spuriously. |
| **Byte-diff goldens** | Any of the above reaching a rendered cell. | §6. |
| **Almanac facts / Records tab** | Consumes extrema and outcomes. | Publishes the above. |

### The same gap, already costing us somewhere else

Found while verifying the 08-24 build, and it is the strongest available
argument that this is not just about the ESPN weekly. `dbt build` warns on
`assert_live_season_has_schedule_capture`, and the offending row is
**`cbs-bsb` 2026, `has_schedule_capture = false`, evidence `unproven`**. All 26
CBS seasons carry no schedule capture; the historical ones are proven complete
by `parsed_final_standings` instead, so only the live one is affected.

The consequence: the rivalry ledger fails closed on an unproven season, so
**CBS's entire 2026 rivalry data has been excluded all season** -- not for a
few hours while a flag lags, but permanently, because CBS has no
schedule-capture mechanism to defer to in the first place. The test exists
precisely because that exclusion is otherwise silent.

This is the landing spot's case in miniature. A design that *derives*
completeness from available evidence -- final standings, game finality, the
schedule -- can classify CBS 2026 instead of excluding it. A design that waits
for a platform's completion flag cannot, because CBS never sends one.

Two consumers are legitimately *supposed* to read incomplete data, and they
are the reason Rule 1 is worth having at all:

- **The weekly recap** -- a narrative product about the week that just
  happened, inherently provisional, and currently blocked entirely.
- **Mid-week / in-flight surfaces** -- a partial week is genuinely useful to
  look at, as long as nothing aggregates it against complete ones.

Both must **say so on their face**. Per the `FA`-band lesson the wording is
load-bearing: "provisional -- corrections pending" states what we know, where
an unlabelled number asserts something false.

---

## 6. The property that makes it safe to ship

**An incomplete unit can never move a golden or set a record.**

If that holds, the blast radius of a misclassification collapses to the one
surface that is supposed to be provisional -- a recap that says "provisional"
and is slightly off, rather than a corrupted record book or a re-anchored
golden.

Pin the byte-diff corpus to complete-only input, and assert it with a test that
has been **observed to fail** before it is trusted (the 08-05 lesson: a guard
never seen to fire is an assumption, not a control). Note also the 08-04
lesson: a golden corpus regenerated from the code under test proves
determinism, not correctness -- so this pinning must be asserted, never
established by a re-anchor.

---

## 7. Open questions -- for Kyle, deliberately not guessed

1. **What proves the correction window elapsed?** Candidates: the next unit
   exists/closed (Kyle's "period before"); `latestScoringPeriod` advanced N
   days past the unit's end; or a re-fetch producing byte-identical values
   twice. The third measures settlement *directly* rather than by proxy --
   which is the entire complaint in §1 -- but costs a second fetch and needs a
   stability window.

   **MEASURED 2026-08-24, and it eliminates one candidate.** The night before,
   ESPN read `currentMatchupPeriod` 20 / `latestScoringPeriod` 152 -- 152 being
   period 20's own last day. When it closed, it read `currentMatchupPeriod` 21 /
   `latestScoringPeriod` **153**. So ESPN advances the scoring frontier and the
   close flag *together*: the "latest advanced past the unit's end" test would
   have fired at the same instant as the close flag and bought **zero lead
   time**. It is a restatement of the platform's flag, not an independent
   proof, so it does not serve the landing spot.

   What *does* have lead time is evidence we already hold and do not consult:
   **`RAW.MLB_GAMELOGS` knows when 08-23's games went final**, from the MLB
   Stats API, hours before ESPN moved anything. That is the landing spot's
   "determine" clause made concrete -- the completeness of a fantasy timeframe
   is derivable from whether the underlying real-world games are over, which is
   a fact about baseball rather than a fact about a vendor's job scheduler. It
   also generalises across platforms for free: it is the same evidence for CBS,
   which sends no completion flag at all (§5).

   Remaining unknown, and it is the real one: MLB game finality proves the
   GAMES are over, not that the platform has finished SCORING them -- which is
   exactly the two-way correction lag of §4. So the likely shape is MLB game
   finality as the early signal, plus a settling margin or a re-fetch stability
   check to cover scoring. Kyle's "plus the period before" may be the cheap
   approximation of that margin.
2. **How many states, and at which grains?** §4 argues for per-grain
   completeness. Minimum viable is probably a boolean at day grain and a
   three-way at period grain (incomplete / complete-unsettled / final). More
   states are easy to add and hard to remove.
3. **Does incomplete data enter the marts** with a state column, or land in a
   parallel provisional surface? The former is far more useful and touches
   every fact; the latter is cheap and safe. This is the main scope call.
4. **Re-extraction and the settled-history guard.** Rule 1 means an unfinished
   period is re-pulled repeatedly and overwritten each time, which is correct
   while it is unfinished and forbidden once it is final -- MLB-188 exists to
   stop a thinner re-fetch replacing stored `clubOfGame` and aged-out free
   agents. The state column is what lets those two rules coexist, and getting
   the handover moment wrong is the most likely way to lose real history.
5. **Do CBS periods share the vocabulary?** CBS runs +2 on ESPN's numbering
   with different close semantics. One shared vocabulary with per-platform
   evidence is the MLB-81/MLB-64 pattern, but it needs saying.
6. **What does the recap say** when its week is incomplete? Wording is product,
   and per §5 it is load-bearing.

---

## 8. Where this touches

Rough seam list, to price it rather than design it.

- **`extract/extract.py`** -- `select_periods`' closed-set test and
  `refuse_unextractable_periods` largely go away for the ingestion path. This
  is the smallest code change and the biggest behavioural one.
- **`extract/matchup_membership.py`** -- already computes nearly everything
  needed; `parse_matchup_membership` would return a *classified* set rather
  than `closed` + `excluded`. `classify_recency` is the precedent for "one
  place the arithmetic lives, with named verdicts" -- and this design must
  **absorb** it, not sit beside it. Its own docstring says two copies of that
  arithmetic is the drifting-twin shape MLB-175 was bitten by, and a second
  lifecycle axis alongside it would be exactly that.
- **RAW** -- state stamped at capture time, because it is a claim about what was
  known *then*. **Serialization point:** a new column or table means
  `config/raw_schema_contract.json` regenerates from Snowflake first. The
  08-11 curveball row documents that trap and the acceptance test on the
  regeneration (exactly one added table plus the timestamp; zero changed, zero
  removed).
- **Marts** -- the state must survive to `dim_matchup_period` and to the facts.
  A fact row that cannot report its own completeness is the `pro_team` bug
  again.
- **Every item in the §5 inventory** -- this is the bulk of the work.
- **Renderers** -- the labels of §5.

---

## 9. What not to do

- Do not delete the aggregation invariant along with the ingestion gate. §3.
- Do not add a `--force`-style flag that admits incomplete data as complete. §3.
- Do not promote on membership shape alone -- keys are not values. §4.
- Do not build a second lifecycle axis beside `classify_recency`. §8.
- Do not let an incomplete unit re-anchor a golden or set a record. §6.
- Do not assume a period-grain flag covers daily claims. §4.
