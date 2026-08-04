# MLB-129 / MLB-188 — Exit 1 via the game-level field: session handoff

**Branch:** `exit1/game-level` (cut from `spike/mlb-129` @ e5bcb2a, PM-confirmed)
**Run:** 2026-08-03, single session · **Commit:** 6133381
**Scope held:** guard + additive field + backfill + verification, then a
Kyle-authorised dev render (§6) and two enumerations (§6, §7). No model, no
sheet, no golden, no push — the render computes from RAW into a mock and
lands nothing. The affinity-chart flip remains wave-end ceremony.

---

## Verdict

**The lift works and the data is in.** Every check either passed or resolved to
a definitional mismatch in the *expectation* rather than a defect in the data —
each one is measured and enumerated below rather than argued.

**Nothing was destroyed.** The backfill is additive by construction: it adds one
key per player entry and assigns nothing else. 194,946 keys added across all 326
RAW rows; **zero** other differences against a pre-backfill clone.

**Three checks came back numerically different from the kickoff's figures.** All
three are expectation-calibration rather than data defects, and §3 shows the
working for each. None was fixed-forward past; Kyle ruled on them after
reading the evidence.

**And the premise behind one of them turned out to be wrong in a useful
direction.** The "24 units of two-club slivers" are not two-club at all. Real-
vs-real is **empty across both seasons** (0 of 94, weight gate off), which
re-rules what the attribution rule actually is — §7.

---

## 1. The verification table

| # | check | expected | measured | |
|---|---|---|---|---|
| 1 | 2025 active-slot weight | 202,547 | **202,547** | ✅ |
| 1 | 2026 active-slot weight | 129,456 | **129,456** | ✅ |
| 2 | Curtis Mead 2026 Boston | 2 PA | **2 PA** | ✅ |
| 2 | — and Washington, unprompted | 327 PA (B-R) | **327 PA** | ✅ |
| 3 | canary: 2026 stored multi-club | 66, unmoved | **66** | ✅ |
| 3 | 2025 stored multi-club | 0 | **0** | ✅ |
| 3 | 2025 club-of-game multi-club | ~199 | **199** swept / 158 blob | ⚠️ see §3a |
| 3 | 2026 club-of-game multi-club | ≥ 66 | **57** vs stored-ex-FA **56** | ⚠️ see §3b |
| 4 | agreement with the spike's sweep | 100% | **99.9889%** (13 of 116,948) | ⚠️ see §3c |
| 5 | preservation of existing values | 0 differences | **0 differences** | ✅ |
| 6 | `dbt parse` | clean | **clean** | ✅ |
| 6 | pure suite | 270 | **282** (+12 new) | ✅ |
| 6 | marts unchanged by the backfill | identical | **identical HASH_AGG** | ✅ |
| 6 | byte-diff goldens | byte-still | **4 pre-existing failures, unmoved** | ⚠️ see §5 |

---

## 2. What the backfill changed, and what it preserved

**Changed — exactly one thing.** A new `clubOfGame` key on every player entry
in every stored box-score blob. 194,946 keys across 326 rows.

**Preserved — everything else, proved rather than asserted.** The check parses
the live blob, deletes `clubOfGame` from it, and asserts the remainder is
**equal as a parsed object** to the pre-backfill clone. Not a row count, not a
checksum of selected columns: the whole structure.

```
rows carrying the new key : 326 of 326
clubOfGame keys added     : 194,946
rows differing otherwise  : 0
```

`loaded_at` survives too, because the backfill `UPDATE`s rather than
delete-then-inserts. That mattered more than it looks: `loaded_at` is the only
remaining evidence of when each period's `proTeam` was actually stamped, and it
is what the 0–28 day staleness measurement below is built on.

**Nothing was deleted at any point.** The destructive loader was never called.

---

## 3. The three that came back different

### 3a. 2025's "199" is a different population, and it reproduces exactly

The spike quoted **199 of 1,471**; I measured **158 of 1,236**. Same rule,
different denominator — 1,471 is the swept kona universe, 1,236 is the players
present in the league's own stored blob with appearances.

Re-measured on the sweep's own population, the count is **199 to the unit**.
Nothing is wrong; the two numbers answer different questions. This is the same
species of mismatch the spike report itself flagged when the brief's tier
counts (957 / 242) did not sum to its own 1,236.

The comparison that carries meaning is unchanged either way: **158 against a
stored stamp that shows 0.**

### 3b. 2026's "≥66" compares against an inflated baseline

2026's stored stamp shows 66 multi-club player-seasons. **10 of those 66 are
`FA`↔club transitions, not trades** — ESPN's record changing between lookback
passes. Excluding them, stored shows **56 real club-to-club moves**.

Club-of-game shows **57**. On like terms it *is* strictly better, by one — the
≥66 threshold was measured against a number that counts FA churn as movement.

The 11 players where stored claims a move and club-of-game does not are the
fix working, not failing:

| player | stored stamp | club of every game he played |
|---|---|---|
| Alek Thomas | Ari, LAD | **Ari** |
| Logan O'Hoppe | LAA, Tex | **LAA** |
| Seranthony Domínguez | ChW, Sea | **ChW** |
| Austin Slater | NYM, TB | *(none — Unattributed)* |

The second club is where ESPN's person record drifted to *after* his last game
in our data. That is precisely the decay this field exists to stop.

### 3c. The 13 sweep disagreements are one shape, and the backfill's answer is better

13 of 116,948 comparable keys (0.011%). **Every one has the identical anatomy:**
two splits for the period, and

- the split the **sweep** picked carries `appliedTotal = 0.0`
- the split the **backfill** picked carries the player's actual production

```
Craig Kimbrel  2026 sp=63    backfill=TB   sweep=NYM
  proTeam=NYM  statSource=0  appliedTotal=0.0
  proTeam=TB   statSource=0  appliedTotal=3.51
```

Root cause, which I predicted from reading both sources before running either:
`extract.py` skips splits whose `stats` object is empty; the sweep counts every
`statSplitTypeId==5` split. So the sweep sees a 1–1 tie and breaks it to payload
order, while the backfill only ever counted the split that produced something.

Attributing a player's production to the club of the game that produced it is
the defensible rule. **I did not change anything to make this check pass** — it
is reported as measured.

**Resolved in §7.** The zero-`appliedTotal` split is not a game at all: it is
the person-level stamp's shadow, an empty-`stats` split for the club ESPN's
person record has already drifted to. All 13 are that one shape, and the
backfill is right in all 13.

---

## 4. The guard, demonstrated

Refusing is the default. `py extract/extract.py --year 2025 --all`, verbatim:

```
========================================================================
REFUSING TO EXTRACT -- 26 settled matchup period(s) in 2025
========================================================================
   period  ended        last loaded
        1  2025-03-30   2026-05-03 20:24:13
       ...
       26  2025-09-28   2026-05-03 20:34:46

Re-extracting them overwrites each player's stored per-day club with
whatever club ESPN reports TODAY. ESPN serves only current club, so
the originals cannot be fetched again -- from ESPN or from here.

Nothing was written. Nothing was deleted.

If you want the club-of-game field on these periods, that is not this
command -- use --backfill-club-of-game, which updates in place, adds
only the new field, and leaves every stored value untouched.

If you truly mean to overwrite the history:
  1. snapshot RAW first  (CREATE TABLE ..._bak CLONE BOX_SCORES)
  2. re-run with --overwrite-day-accurate-history
========================================================================
```

Exit code 1. Nothing written.

**The guard is not a bare presence check, and that was a deliberate call you
ruled on.** A literal one would refuse on the routine weekly run, because
`get_recent_matchup_periods` re-extracts completed periods inside a 21-day
window *on purpose* — that is how the stamps get captured at all. The flag
would have gone permanently on inside a fortnight, which is the check_pii
failure MLB-188 names. So the exemption is freshness, both readers take the 21
from one constant, and the whole invocation is refused rather than any part of
it half-run.

Nine polarity cases are pinned in `tests/test_extract_club_of_game.py`,
including the one that matters most: the default weekly run's period set comes
back clean.

---

## 5. The goldens — a pre-existing failure, and why it is not this work

The byte-diff suite **was already failing before I touched anything**: 4 failed,
13 passed, on a baseline run taken before the backfill.

It was run again after the backfill and returned **the same 4 failures and the
same 13 passes** — same test names, no movement in either direction:

| run | result |
|---|---|
| before the backfill | 4 failed, 13 passed |
| after the backfill | 4 failed, 13 passed *(same 4)* |

Taking the baseline first is what makes that statement worth anything; without
it there would be no way to tell an inherited failure from one I caused.

It is not this session's doing, and that is measurable rather than arguable:

- the ANALYTICS marts were last altered **2026-08-02 23:04**, roughly a day
  before this session began;
- `dbt build` was never run here — only `dbt parse`, which writes no tables;
- `HASH_AGG(*)` over `FCT_PLAYER_DAILY_PERFORMANCE` (1,097,604 rows),
  `FCT_PLAYER_WEEKLY_ACTIVE_PERFORMANCE` and `FCT_TEAM_SEASON_PERFORMANCE` is
  **identical before and after the backfill**. A golden built from an unchanged
  mart cannot move.

The cause is ordinary staleness: fixtures anchored ~Jul 27, two further weeks of
2026 data landed 08-02, and season records moved with it — e.g. `Hits: 89 …
Week 10 of 2026` → `90 … Week 17 of 2026`.

**This needs a separate re-anchor decision from you.** It also means check 6
cannot be read as "goldens byte-still" literally; the honest form is "the same
failures, unmoved," which is what was measured.

---

## 6. The chart, dev-rendered post-flip (scope extension, Kyle 08-03)

Computed directly from RAW at `get_team_affinity_weights`' exact grain and
fed to a mock of the chart block — no model, no sheet, no golden. 2025 and
2026 blocks, each paired with all-time. Rendered locally rather than
published: the columns are real team abbrevs.

### Measurement 1 — the Unattributed band goes to zero

| scope | total weight | band post-flip | | band today |
|---|---|---|---|---|
| 2025 | 202,547 | **0.0** | 0.0000% | 23,749 (11.73%) |
| 2026 | 129,456 | **0.0** | 0.0000% | 30 (0.02%) |
| all-time | 332,003 | **0.0** | 0.0000% | 23,779 (7.16%) |

Exactly zero, not a trace. The "today" column reproduces this document's own
11.73% / 0.02% figures to the unit, which is what says the computation is
sitting at the right grain rather than merely producing a plausible number.

**The band does not shrink — it disappears.** `UNATTRIBUTED` never enters the
club list, so no row is emitted: each block carries exactly 30 rows, one per
MLB club. Two display consequences ride on the flip. The shipped explainer's
closing sentence about Unattributed becomes dead text, and the reserved
vocabulary needs somewhere to live if a future season reintroduces an
unattributable unit — the word must not quietly come to mean something else.

### Measurement 2 — the unrostered corner

**The chart's own scope is clean.** 55,557 active-slot production rows
carrying all 332,003 weight: 0 with no blob entry, 0 with a null club.

**The deficiency is real but confined to FA-slot rows:** 476 player-days,
60 players, 2,186 involvement units, all 2026. RAW source is
`RAW.BOX_SCORES.raw_json:free_agents[]`, the kona anti-join population. They
carry **zero** chart weight (`active_weight` is 0 on FA slots).

Two sub-causes, both measured:

| player | ESPN id | 2025 rows / null | 2026 rows / null | served by today's kona |
|---|---|---|---|---|
| Andrew Morris | 5007765 | — | 22 / **0** | yes, 65 periods |
| Andrew Morris | 5266690 | — | 14 / **14** | **no** |
| Paxton Schultz | 4313208 | 13 / 0 | 27 / **27** | 2025 only |
| Jake Woodford | 35284 | 22 / 0 | 16 / **16** | 2025 only |

1. **Stale duplicate ESPN ids** — the collision class the spike enumerated.
2. **Players no longer in today's 2026 kona universe** — same id, same
   endpoint, same `limit:1500 sortPercOwned`; today's response returns them
   for 2025 periods and not 2026.

**This deficiency decays.** The backfill can only attribute players ESPN
still returns for that period, so the gap between a period being lived and
being backfilled is itself the loss function. Same species as MLB-188.

Sizing, for whoever takes the ticket: the fix is plumbing, not research. The
MLB-129 crosswalk resolves ESPN id → MLBAM at 100% coverage and the MLB
gamelog spine carries club-of-game per player-day. The crosswalk turned out
not to be needed for Exit 1; this is what it *is* for.

---

## 7. The two-club units — enumerated, and the rule they re-ruled

### Real-vs-real is empty

Every player-period in 2025+2026 whose splits name more than one club, scanned
**with the weight gate off**:

| | count |
|---|---|
| >1 club on ANY split (the spike's definition) | **94** |
| — of which carry active-slot weight | 2 (**24 units**) |
| — at zero weight, i.e. invisible behind the gate | 92 |
| **>1 club among splits carrying any stats** | **0** |
| **>1 club among splits carrying PA or BF** | **0** |

Shapes across all 94: `(0 producing, 2 stat-less)` 65 · `(1, 1)` 29 ·
`(2, …)` **0**. Nothing hides at zero weight, and nothing is a two-club day.

### The 24 units, in full

Both 2026, both a single ESPN id, no duplicates.

**David Peterson** — id `40921` · sp 89 → **2026-06-21** · weight **20.0**

| split | club | PA | BF | appliedTotal | stat keys | gameId |
|---|---|---|---|---|---|---|
| producing | **NYM** | 0 | **20** | −0.96 | 44 | 401815842 |
| stat-less | ChC | 0 | 0 | 0.0 | **0 — `{}`** | 401815848 |

`OUTS 12, P_H 6, P_BB 2, ER 4, K 5`. Stored `clubOfGame` = **NYM**; the
person-level stamp says `ChC`. All 20 units are NYM's.

**Curtis Mead** — id `42360` · sp 119 → **2026-07-21** · weight **4.0**

| split | club | PA | BF | appliedTotal | stat keys | gameId |
|---|---|---|---|---|---|---|
| producing | **Wsh** | **4** | 0 | −2.6 | 42 | 401816212 |
| stat-less | Bos | 0 | 0 | 0.0 | **0 — `{}`** | 401816201 |

Stored `clubOfGame` = **Wsh**; the stamp says `Bos`. All 4 units are Wsh's.
20 + 4 = 24.

Both verified against the MLB spine: Peterson `mlbam 656849`, Mets vs
Phillies, 12 outs / 6 H / 5 K / 4 ER; Mead, Nationals vs Rockies, 4 AB / 0 H.
In both, **the phantom club was idle that date** — 28 teams have spine rows on
2026-06-21 (Cubs and Blue Jays off), and Boston has none on 2026-07-21.

### Mechanism — the phantom shadow

Not duplicate ids: 0 of 94 candidates appear under more than one ESPN id, and
the collision class's 25 units sit entirely on Andrew Morris id `5007765` as
ordinary weight across 28 periods, with the duplicate `5266690` and the other
four collision ids at 0 each. Morris never appears among the 94. **The 24/25
adjacency is coincidence** — worth recording, because it is exactly the kind
of near-match that invites a false causal story.

Not same-day two-club play, which no candidate exhibits and which the idle
clubs rule out. Not a suspended-game or stat-filing artifact either: nothing
is credited to an earlier date under a later club, because the phantom split
carries no stats at all.

What it is: **person-record drift reaching split level.** ESPN moves the
person record to the incoming club during a transition window and emits a
split for that club carrying an empty `stats` object — 123 of 159 stat-less
splits name a club that did play (the "roster-days, not games" artifact), 36
name a club that did not play at all. In both weighted cases the person-level
stamp names the *same* club as the phantom split, which is the tell: the
phantom is the stamp's shadow, one layer down.

**The production filter is what launders it.** Requiring a non-empty `stats`
object removes the shadow before any attribution rule sees it — and that is
also the whole of §3c: the spike's sweep had no such filter, so its majority
rule ranked a phantom equal to a real game and payload order decided. All 13
disagreements are that, and the backfill is right in all 13.

### The re-ruling

- **The producing-splits filter is the operative attribution rule.** It, not
  the tie-break, is what decides these rows, and it resolves both weighted
  cases to the club that actually played.
- **Majority-by-production is a dormant fallback.** It has never arbitrated
  between two real clubs, because that class is **unobserved across two full
  seasons** (0 of 94, gate off). It stays in place, correctly specified, for
  a case that has not yet happened.
- Anyone tempted to re-tune the tie-break should note it is not load-bearing
  today. The thing doing the work is the filter above it.

---

## 8. Exact commands run

```bash
# Day 1 — guard + lift (committed as 6133381)
py -m pytest tests/ -q                              # 282 passed, 2 known-WIP
py extract/extract.py --year 2025 --all             # guard refusal, exit 1

# snapshot, and prove it restores
CREATE TABLE IF NOT EXISTS ESPN_FANTASY.RAW.BOX_SCORES_BAK_20260803_MLB129
  CLONE ESPN_FANTASY.RAW.BOX_SCORES;
CREATE OR REPLACE TABLE ...RESTORE_REHEARSAL CLONE ...BAK;   # fingerprints match
DROP TABLE ...RESTORE_REHEARSAL;

# dry run on one matchup period, verified before the other 319
py extract/extract.py --year 2025 18 --backfill-club-of-game

# independent cross-check data (detached, 326/326, zero failures, ~9.5 min)
py docs/archive/mlb129-spike/sweep_gamelevel_club.py <outdir>

# the backfill (detached, ~16.5 min, both seasons exit 0)
py extract/extract.py --year 2025 --all --backfill-club-of-game
py extract/extract.py --year 2026 --all --backfill-club-of-game

# gates
py -m pytest tests/ -q ; py -m pytest tests/ -m warehouse -q
cd dbt_league && dbt parse
```

**Restore, if it is ever wanted:**

```sql
CREATE OR REPLACE TABLE ESPN_FANTASY.RAW.BOX_SCORES
  CLONE ESPN_FANTASY.RAW.BOX_SCORES_BAK_20260803_MLB129;
```

The snapshot is intact and untouched. Reverting costs one statement and loses
only the added key.

---

## 9. Confidence, with the failure modes named

**High that the field is right.** Mead reconciles to Baseball Reference on both
sides of his move without being tuned to (2 and 327). The 2025 deadline
reconstructs on the exact scoring-period boundary for three independent players
whose stored stamps are all their *2026* clubs. Coverage of players who
actually played is 100% on every period sampled.

**High that nothing was damaged.** Preservation is exact across all 326 rows,
the marts hash identically, and the snapshot restores.

What could still be wrong:

1. **The 13 disagreements are a rule choice, not a bug — but it is a rule
   choice.** If anyone later decides a stat-less split is valid club evidence,
   these 13 flip. Document the rule wherever the field is consumed.
2. **`clubOfGame` is in RAW and nowhere else.** No dbt model reads it, so
   nothing downstream is validated yet. The chart flip is where this gets
   exercised for real, and that is where a mart-level surprise would surface.
3. **2026 keeps moving.** Its multi-club counts will drift as trades happen;
   57 is a reading, not a constant.
4. **The sweep used `limit=1500`**, inherited from the spike. No weighted row
   fell outside it, but a busier day could.
5. **The 0–28 day staleness figure describes today's history.** It is a
   property of when the weekly run happened to fire, not a guarantee.

**What I did not do:** touch the affinity query, any mart, any render, any
golden, or push anything. The chart still reads `pro_team`, which is why the
goldens could not move.

---

## 10. Open, and needing your call

**Ruled and closed during the session:**

- The three ⚠️ rows: read as expectation-calibration, not data defects.
- The Exit-1 route, the guard's freshness exemption, and byte-for-byte
  preservation of the person-level stamps (Kyle, 08-03).
- The attribution rule, re-ruled off §7's enumeration: producing-splits
  filter operative, majority-by-production dormant.

**Still open:**

1. **The stale goldens** need a re-anchor decision, independent of this work.
   They were failing before this session and are unmoved by it (§5).
2. **The MLB-131 inventory comment** is owed: these RAW payloads are
   temporally irreplaceable and cannot be re-fetched from ESPN, ever. The PM
   posts it; I have not written to Linear.
3. **The 476 unclubbable FA player-days** (§6) are a ticket by Kyle's ruling
   — "you can't hit a double in the MLB without being on an MLB team". Route
   them through the MLB-129 crosswalk. Note the population grows the longer
   a period waits to be backfilled.
4. **The band's vocabulary after the flip** (§6): `Unattributed` renders no
   row at all, and the explainer sentence describing it becomes dead text.
   The word is reserved (MLB-159) and must not drift to a new meaning.
5. **The `platform_player_id` vs `name_key` shape call** from the spike is
   still open and untouched here.
6. **The chart flip itself** remains wave-end ceremony. §6 is the dev render
   the display ruling was taken from.
