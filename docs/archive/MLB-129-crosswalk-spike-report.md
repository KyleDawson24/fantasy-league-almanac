# MLB-129 spike report — the ESPN→MLBAM crosswalk, and a cheaper road to Exit 1

**Branch:** `spike/mlb-129` · **Run:** 2026-08-03 · **Checkpoint:** EOD Aug 5
**Scope held:** mapping and evidence only. No join, no mart, no render, no push.

---

## Verdict

**GO on the crosswalk — all three conditions met, and by a wide margin.**

But the more consequential result is that **the crosswalk turned out not to be
what Exit 1 was blocked on.** ESPN already sends club-of-game, per scoring
period, on the payload the weekly extract already fetches. `extract.py` walks
those very splits for stats and drops the club field on the floor.

Both findings are below, each with its evidence and its failure modes.

---

## 1. Step 0 — does ESPN carry an external id?

**No.** Not MLBAM, not bbref, not retro.

RAW could not answer this, and that matters: `extract.py:307-315` hand-picks
seven keys per player and discards the rest of the kona object, so the
warehouse holds a projection, not ESPN's payload. The check had to run against
the live API.

Probed `kona_player_info` (1,500 players × 2025 and 2026) and
`kona_playercard`, enumerating every key path:

- exactly one key name hints at an external id — `player.stats[].externalId`
- its values are a **season string** (`'2026'`) or an **ESPN event id**
  (`'401816219'`). Never an MLBAM id.
- **zero** occurrences of known MLBAM ids (Ohtani 660271, Judge 592450,
  Soto 665742, Witt 677951, Yamamoto 808967, Skubal 669373) as values anywhere
  in either season's payload.

The crosswalk is a match, not a parse. The brief's premise holds here.

---

## 2. The crosswalk — built, measured, independently audited

Reuses `extract/mlb_crosswalk.py`'s matcher **by import**, not by copy —
`norm`, `initial_key`, `build_mlb_index`, `learn_team_map`, `match`. MLB-175
records what a hand-rolled twin of an existing key costs; nothing here
re-implements name logic.

**Team evidence, and why the brief's tier-B workaround was unnecessary.** The
brief warned that ESPN's 2025 club labels are corrupt (0 of 1,236
player-seasons carry >1 club), so 2025 team evidence had to be excluded and
tier B needed MLBAM's own team as a tiebreak. That is true of ESPN's
**player-level** stamp. It is not true of the **game-level** stamp (§3), which
is period-accurate for 2025. So both tiers got real ESPN team evidence.

### Coverage — by player count and by 2025 active-slot weight

| tier | players | resolved | 2025 weight | resolved | coverage |
|---|---|---|---|---|---|
| **A — 2026-anchored** | 561 | 561 | 169,187 | 169,187 | **100.00%** |
| **B — no 2026 anchor** | 675 | 675 | 33,360 | 33,360 | **100.00%** |
| **all 2025** | 1,236 | 1,236 | 202,547 | 202,547 | **100.00%** |
| all ESPN players | 1,487 | 1,487 | — | — | **100.00%** |

Method distribution across all 1,487: `name_unique` 1,477 · `name_team_season`
7 · `fuzzy_unique` 3.

> **Note on the tier player counts.** The brief's table gives tier A 957 / tier
> B 242, which do not sum to its own 1,236 total. Reproducing both definitions
> shows why: **957 counts players with *any* 2026 row, while 242 counts against
> a 2026 *active-slot* anchor** — the columns are on different definitions. The
> **weights are sound under the active-slot anchor and reproduce exactly**
> (169,187 / 33,360 / 16.47%), which is what matters, since the bar reads
> against weight. This report uses the active-slot anchor throughout, so its
> player counts are 561 / 675.

### Why 100% is credible — the independent audit

1,477 of 1,487 matched on `name_unique`, which checks a unique exact name plus
season overlap and **does not look at team**. A 100% off a name-only bar needs
auditing against evidence the matcher never consumed, so:

- for every matched player, compare **ESPN's game-level club set** for a season
  against **MLBAM's season team** for the matched id — two independent sources
- **99.92% agreement (2,382 / 2,384)** on comparable player-seasons
- **both disagreements are 2026** in-progress trade-timing (statsapi's
  `currentTeam` moves under a live season), and both matched the correct
  person. **2025 — the season Exit 1 needs — has zero disagreements.**

The structural reason it is this easy, measured rather than assumed: inside a
2025-26 window only **3 normalized names of 1,729 (0.17%)** map to more than
one MLBAM id. The brief's hypothesis was right — two seasons of clean current
names is a different problem from CBS's 26 seasons of drift. The Vladimir
Guerrero Jr./Sr. class is *structurally* excluded, because a two-season index
cannot contain the father.

### Unresolved — condition 2

**Empty.** Zero unresolved players, zero unresolved weight, both tiers.

The residual that exists instead is three **collisions** — one MLBAM id claimed
by two ESPN ids, all enumerated:

| mlbam | ESPN ids | combined weight |
|---|---|---|
| 686930 | 4620704, 5195197 — Mason Barnett | 0 |
| 682989 | 5127102, 4683391 — Victor Mederos | 0 |
| 702193 | 5266690, 5007765 — Andrew Morris | 25 |

These are ESPN duplicate records for one human. At **person grain that is
correct behaviour** — two platform ids, one person — and it is exactly the
person-vs-asset distinction MLB-129's body calls for. 25 units of 332,003
(0.008%).

---

## 3. The finding that changes the premise — ESPN already sends club-of-game

`extract.py:271-292` loops over the per-scoring-period splits
(`statSplitTypeId == 5`), reads `stats` and `appliedTotal`, and **never touches
`split["proTeamId"]`** — the club of *that game*. The stored `pro_team` comes
instead from `player.get("proTeamId")` at line 312: the person-level current
club, which is the whole MLB-159 defect.

### It survives the ticket's own falsifier

MLB-159's confirming query is the test: a real club signal must show
mid-season trades; a frozen one cannot.

| signal | 2025 player-seasons with >1 club |
|---|---|
| stored player-level stamp | **0** of 1,236 |
| ESPN game-level stamp | **199** of 1,471 |

The movers are real, checkable 2025 transactions — Rafael Devers `Bos→SF`,
Cedric Mullins `Bal→NYM`, Ryan O'Hearn and Ramón Laureano `Bal→SD`, Ryan
Helsley `StL→NYM`, Charlie Morton `Bal→Det→Atl`. The Baltimore cluster is the
2025 deadline selloff, reconstructed correctly.

### It is day-accurate, pinned to real trade dates

ESPN scoring periods are calendar days. Anchoring period 10 to 2025-03-27
(opening day) is self-validating: it puts period 195 at **2025-09-28**, the
season's actual final day. Then —

| player(s) | last old-club period | first new-club period | real trade date |
|---|---|---|---|
| Rafael Devers | 90 = **2025-06-15** | 92 = 2025-06-17 | **2025-06-15** |
| Mullins · Helsley · O'Hearn | 135 = **2025-07-30** | 137 = **2025-08-01** | **2025-07-31** (deadline) |

Three same-day deadline trades share the identical boundary; Devers sits on a
different, earlier one. That is three independent confirmations of the anchor.

### It reconciles MLB-190's Baseball Reference pin exactly

| Curtis Mead 2026, Boston | PA |
|---|---|
| Baseball Reference (truth) | **2** |
| warehouse today | 20 |
| game-level club-of-game | **2** ✅ |

This is the case MLB-190 used to disprove *"2026 is live-extracted and
correct."* Game-level attribution reproduces the external ground truth to the
unit.

### Coverage of the affinity chart's own metric

Measured at the exact grain and with the exact weight expression
`get_team_affinity_weights` uses:

| bucket | total weight | resolved to a club-of-game | coverage | unresolved |
|---|---|---|---|---|
| 2025 tier A | 169,187 | 169,187 | **100.00%** | 0 |
| 2025 tier B | 33,360 | 33,360 | **100.00%** | 0 |
| **2025 all** | **202,547** | **202,547** | **100.00%** | **0** |
| 2026 all | 129,456 | 129,456 | **100.00%** | 0 |

Weight sitting on a period where the player played for two clubs: **24 units of
332,003 (0.007%)** — so day-grain attribution is unambiguous in practice.

### How wrong the chart is today

**45,059 of 202,547 units of 2025 weight — 22.25% — are filed under the wrong
club.** MLB-159 estimated a ≥13.5% floor with an unbounded ceiling; the
measured figure is 22.25%. The `FA` bucket MLB-190 already discloses is 11.73%
of it; the other ~10.5% is silent mis-attribution. 2026 disagrees on 252 units
(0.19%), consistent with MLB-190's 0.12% period-transition measurement.

---

## 4. What neither path escapes — `game_date` is NULL on every ESPN row

MLB-159 specifies the crosswalk join as:

```
fct_player_daily_performance → dim_player_identity → mlbam_id + game_date
    → stg_mlb__player_game → team_id
```

**That join cannot execute as written.** `game_date` is NULL on 100% of ESPN
rows — 0 non-null of 117,454 — and it is NULL *by construction*, not by
accident: `int_player_daily.sql:357` is `cast(null as date) as game_date`, and
line 14 documents it — *"NULL on ESPN rows — their day identity is the platform
scoring_period."*

So a perfect crosswalk still does not complete Exit 1. A `scoring_period →
calendar date` derivation is also required, and nobody has costed it. This
spike derived one and validated it three ways (§3), but it is an additional
step and it is precisely the kind of arithmetic assumption this ticket family
has been burned by.

**The game-level route does not need it at all** — the club arrives already
attached to the scoring period.

---

## 5. Assertions — condition 3

`assert_spike.py`, 15 assertions, **all passing**, exit 0:

- every row is resolved-int or unresolved-None — no third state (1,487 resolved)
- **no row resolved by a tie or weak rule** — the silent-guess path fired 0 times
- audit ran against independent evidence (2,382 agreeing player-seasons)
- zero 2025 identity disagreements
- collisions enumerated, bounded (3 / 25 units)
- 100% club-of-game coverage on all four buckets, residual empty and enumerable
- all four weight totals reconcile to the figures the tickets state
  independently (202,547 / 169,187 / 33,360 / 129,456)
- game-level club is provably independent of `pro_team` — they differ on 45,311
  units, so `pro_team` was not silently reused as the answer

**One hardening required before any production landing.** The imported
`match()` returns a *guess* on a true tie (`method='name_ambiguous'`) rather
than NULL. It fired zero times on this population — but that is a property of
the data, not a guarantee of the code. Condition 3 demands it be NULLed
explicitly. **Do not land this without that change.**

---

## 6. Confidence, with the failure modes named

**High on the crosswalk** (100%, independently corroborated at 99.92%, on a
population whose homonym density is 0.17%). **High on club-of-game for 2025**
(100% coverage, three date pins, an exact Baseball Reference reconciliation,
and the ticket's own falsifier failing to falsify).

What could still be wrong:

1. **The game-level field is a live-API finding, not warehouse data.** Acting
   on it requires re-extracting 2025 and 2026. **MLB-189 is a real blocker**:
   re-extracting an already-loaded period today would silently destroy 2026's
   club dimension, with no guard and no recovery. The guard must land first.
   (An extract that writes club-of-game is strictly better than what it
   overwrites — but "strictly better" is a claim to verify, not to assume.)
2. **The `scoring_period → date` anchor** is validated three ways but is still
   an arithmetic derivation. Only the game-level route avoids depending on it.
3. **Periods-with-a-split are roster-days, not games.** Immaterial to coverage
   (which is measured only on rows carrying weight), but it makes the raw
   period counts in §3 the wrong unit for anything else.
4. **Sweep used `limit=1500`.** No warehouse-weighted row fell outside it in
   either season, but a larger league or a busier day could.
5. **2026 is in progress**, so its two audit disagreements will keep churning
   as trades happen. Not an identity defect.

**What I did not do:** touch the affinity query, any mart, any render, or the
shipped Exit-2 work. No dbt model changed, so goldens are byte-still by
construction.

---

## 7. Recommendation

Both conditions of the ladder's GO branch are satisfied, so **Exit 1 can make
2.0** on the measurement. But the two routes are not equally priced, and the
cheaper one is not the one the ticket assumed:

| | crosswalk route | game-level route |
|---|---|---|
| identity resolution | ✅ built, 100% | not needed |
| `scoring_period → date` | **required, uncosted** | not needed |
| extract change | none | lift one field already in the loop |
| re-extract 2025+2026 | not needed | **required — gated on MLB-189** |
| accuracy | club-of-*day* via MLB spine | club-of-*game* from ESPN, day-accurate |

**Recommend: land MLB-189's guard, then the game-level route for Exit 1, and
keep the crosswalk.** The crosswalk is not wasted — MLB-129's actual goal is
one universal player table for cross-league work (MLB-124's dictionary,
combined record books), which the affinity chart was only one consumer of. It
is built, audited, and costs nothing further to keep.

**A decision this spike cannot make:** whether ESPN entries in
`dim_player_identity` should be keyed on `platform_player_id` rather than
`name_key`. ESPN's ids are stable across both seasons, so an id-keyed entry is
strictly stronger than CBS's name-keyed one — but that is a dimension-shape
change with MLB-158-B implications, and it is Kyle's and the PM's call.

---

## Artifacts

Scripts are on `spike/mlb-129` under `docs/archive/mlb129-spike/`:
`step0_probe.py` · `sweep_gamelevel_club.py` · `measure_gamelevel_coverage.py`
· `falsify_gamelevel.py` · `reconcile_mead.py` · `espn_crosswalk_spike.py` ·
`audit_crosswalk.py` · `assert_spike.py`.

They live under `docs/archive/` because CLAUDE.md forbids defaulting a new
artifact to the repository root and this is spike exhaust, not maintained
tooling. **If you would rather they sat elsewhere, say so** — nothing imports
them and moving them is free.

Each takes the output directory as `argv[1]` and writes there. Order:
`sweep_gamelevel_club.py` → `measure_gamelevel_coverage.py` →
`espn_crosswalk_spike.py` → `audit_crosswalk.py` → `assert_spike.py`. The sweep
is idempotent and resumable (326 periods, ~11 min, zero failures); the rest are
seconds. Data outputs (85 MB of NDJSON) are deliberately **not** committed —
re-run the sweep to regenerate.
