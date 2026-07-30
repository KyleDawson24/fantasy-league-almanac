# DAG boundaries — target-state contract (DRAFT)

**Status: DRAFT.** MLB-158 Phase A. The epic has had shape-level approval
only; nothing here is decided. Every violation below is written up as
**options with trade-offs**, and the option lists are the point of the
document — they are what turns "the layers are dishonest" into a set of
calls Kyle can actually make.

Nothing in this document has been implemented. No model has moved.

*Written 2026-07-30 against `bad68b2` + the MLB-153 docs commit. Model
inventory and every dependency edge below are derived from the parsed
manifest, not from reading prose.*

---

## 1. The finding, restated precisely

Directory and prefix names in this project encode a **false topological
order**. A reader who assumes `stg_` → `int_` → `dim_`/`fct_` → `mart_`
is a dependency order will be wrong seven times.

Derived from the manifest (`dbt parse` at HEAD, then walking
`depends_on` for every model), there are exactly **7 backward edges** —
edges where a model reads something its own layer name says is
downstream of it:

| # | Reader | Layer | Reads | Layer | Evidence |
|---|---|---|---|---|---|
| 1 | `int_cbs__lineup_intervals` | intermediate | `dim_player_identity` | core | [`int_cbs__lineup_intervals.sql:78`](../dbt_league/models/intermediate/int_cbs__lineup_intervals.sql#L78) |
| 2 | `int_cbs__roster_stints` | intermediate | `dim_player_identity` | core | [`int_cbs__roster_stints.sql:615`](../dbt_league/models/intermediate/int_cbs__roster_stints.sql#L615) |
| 3 | `int_player_identity_context` | intermediate | `dim_player_identity` | core | [`int_player_identity_context.sql:58`](../dbt_league/models/intermediate/int_player_identity_context.sql#L58) |
| 4 | `int_cbs__player_daily` | intermediate | `fct_cbs_player_game_attribution` | core | [`int_cbs__player_daily.sql:60`](../dbt_league/models/intermediate/int_cbs__player_daily.sql#L60) |
| 5 | `int_cbs__player_daily` | intermediate | `dim_team_owner` | core | [`int_cbs__player_daily.sql:136`](../dbt_league/models/intermediate/int_cbs__player_daily.sql#L136) |
| 6 | `stg_cbs__player_season_stats` | staging | `dim_stat` | core | [`stg_cbs__player_season_stats.sql:88`](../dbt_league/models/staging/stg_cbs__player_season_stats.sql#L88) |
| 7 | `stg_cbs__team_owners` | staging | `int_cbs__team_owner_season` | intermediate | [`stg_cbs__team_owners.sql:28`](../dbt_league/models/staging/stg_cbs__team_owners.sql#L28) |

There are also 34 same-layer edges (staging→staging, core→core, etc.).
Those are **not** violations and are excluded throughout; a layer is
allowed to have internal order.

### Two corrections to the epic's own citations

Worth knowing before the epic review, since both appear in MLB-158's
description:

- The epic cites `stg_cbs__player_season_stats.sql:83`. The `dim_stat`
  join is at **line 88**. Line drift, same finding.
- The epic characterises both staging violations as "staging reads
  dims." Only #6 reads a dim. **#7 reads an intermediate**
  (`int_cbs__team_owner_season`) — and, more interestingly,
  `stg_cbs__team_owners` reads **no source at all**. It is not a staging
  model that reached too far downstream; it is a model that was never
  staging. That changes which remedy fits (§4.5).

### The ping-pong is real and it is one path

"int↔core ping-pong" resolves to a single concrete chain:

```
int_cbs__player_game_points  (intermediate)
        ↓
fct_cbs_player_game_attribution  (core)
        ↓
int_cbs__player_daily  (intermediate)   ← edge #4
        ↓
int_player_daily  (intermediate)
        ↓
fct_player_daily_performance  (core)
```

The graph descends into core, climbs back out to intermediate, and
descends again. It is acyclic — dbt would refuse to build a true cycle —
but no naming convention survives it.

---

## 2. The layers we actually have

The epic proposes six layers. Working through all 72 models, the six do
account for every one, and the boundaries fall in defensible places.

| # | Layer | What defines membership | Models |
|---|---|---|---|
| 1 | **source / staging** | Reads `source()`. 1:1 reshape: flatten, type, rename. No cross-entity logic. | 21 |
| 2 | **platform reconstruction** | Rebuilds state the platform never served cleanly — CBS's roster stints, lineup intervals, eligibility windows, per-game repricing. Platform-specific by nature. | 6 |
| 3 | **identity** | Answers *who is this* — player, franchise, owner. Consumed by everything downstream **and** by reconstruction. | 8 |
| 4 | **convergence** | Where ESPN and CBS become one column contract. The union seam. | 3 |
| 5 | **core** | The star-schema contract. Grain-documented dims and facts that marts and Python rely on. | 18 |
| 6 | **reporting** | Consumer-shaped derivations. | 16 |

**The load-bearing claim: identity is upstream of reconstruction, not
downstream of it.** You cannot reconstruct which franchise rostered a
player on a given day in 2007 without first knowing which player the
1999-era name string refers to. Today identity's output
(`dim_player_identity`) lives in core, *below* reconstruction, which is
precisely why edges #1–#3 exist. They are not sloppiness; they are the
graph telling us the layer assignment is wrong.

That single relocation accounts for **3 of the 7 violations**, and it is
the reason the epic's layer list is worth adopting rather than just
renaming things.

---

## 3. Every model, mapped

72 models. Target home per the six-layer scheme. **⚠ = moves layer**;
everything else keeps its current home under the new names.

### Layer 1 — source / staging (21)

All read `source()` and reshape 1:1. No proposed moves.

`stg_box_scores` · `stg_cbs__draft` · `stg_cbs__mlbam_crosswalk` ·
`stg_cbs__player_season_stats` ⚠(keeps layer; loses one edge — §4.4) ·
`stg_cbs__rosters` · `stg_cbs__scoring_settings` · `stg_cbs__standings` ·
`stg_cbs__ui_rosters` · `stg_cbs__ui_standings` ·
`stg_cbs__ui_transactions` · `stg_draft` · `stg_matchup_pairs` ·
`stg_matchup_scores` · `stg_mlb__fielding_seasons` ·
`stg_mlb__game_positions` · `stg_mlb__player_game` ·
`stg_player_stat_breakdowns` · `stg_roster_settings` ·
`stg_scoring_settings` · `stg_team_owners` · `stg_transactions`

> `stg_player_stat_breakdowns` reads `stg_box_scores`, not a source
> directly. Same-layer, and the box-score VARIANT genuinely carries two
> grains — this is the documented multi-grain fan-out, not a violation.

### Layer 2 — platform reconstruction (6)

The CBS walk-back. Everything here exists because CBS's fantasy layer is
current-season-only and the history had to be rebuilt from UI archives.

| Model | Current | Note |
|---|---|---|
| `int_cbs__draft_picks` | intermediate | The MLB-90 assembly port |
| `int_cbs__eligibility_windows` | intermediate | Date-scoped eligibility from the captured rule |
| `int_cbs__lineup_intervals` | intermediate | ⚠ loses edge #1 |
| `int_cbs__player_game_points` | intermediate | The MLB-62 repricing |
| `int_cbs__roster_stints` | intermediate | The walk-back proper; ⚠ loses edge #2 |
| `int_cbs__roster_stints_effective` | intermediate | End-exclusive arithmetic |

### Layer 3 — identity (8) ⚠ new layer

| Model | Current | Moves? |
|---|---|---|
| `int_player_identity_candidates` | intermediate | name only |
| `dim_player_identity` | **marts/core** | ⚠ **moves out of core** — the keystone |
| `int_player_identity_context` | intermediate | name only; ⚠ loses edge #3 |
| `int_cbs__player_name_ids` | intermediate | name only |
| `int_franchise_registry` | intermediate | name only |
| `int_franchise_seasons` | intermediate | name only |
| `int_cbs__team_owner_season` | intermediate | name only |
| `stg_cbs__team_owners` | **staging** | ⚠ **moves out of staging** — §4.5 |

The layer is internally ordered and that is fine: `candidates` →
`dim_player_identity` → `context`. A layer may have an internal chain; it
may not have a backward edge to a *later* layer.

**Open: do the identity *contracts* move too?** `dim_franchise`,
`dim_franchise_season`, `dim_owner`, `dim_team_owner` are identity by
subject matter but contract-shaped by role. The table above leaves all
four in core. See §4.6 — this is a genuine open call, not an oversight.

### Layer 4 — convergence (3)

The seam where two platforms become one contract. Small, and that is the
point — if convergence sprawls, the union is leaking.

| Model | Current | Note |
|---|---|---|
| `int_cbs__player_daily` | intermediate | CBS day-grain in `int_player_daily`'s exact column contract; ⚠ loses edges #4, #5 |
| `int_player_daily` | intermediate | The ESPN + CBS union |
| `int_cbs__player_season_stats` | intermediate | Season-grain, stat_name-bridged — the record book's source |

### Layer 5 — core (18)

`dim_franchise` · `dim_franchise_season` · `dim_matchup_period` ·
`dim_owner` · `dim_roster_slot_counts` · `dim_stat` · `dim_team_owner` ·
`fct_cbs_player_game_attribution` · `fct_player_daily_performance` ·
`fct_player_position_pts` · `fct_player_season_performance` ·
`fct_player_weekly_active_performance` ·
`fct_player_weekly_inactive_performance` ·
`fct_player_weekly_slot_performance` · `fct_roster_stints` ·
`fct_team_season_performance` · `fct_team_weekly_active_performance` ·
`fct_team_weekly_inactive_performance`

(19 today, minus `dim_player_identity` to identity.)

### Layer 6 — reporting (16)

`mart_cbs_draft_recap` · `mart_cbs_draft_zip_fill` ·
`mart_daily_roster_snapshot` · `mart_draft_board` ·
`mart_league_weekly_benchmarks` · `mart_period_standings` ·
`mart_player_career_records` · `mart_player_fpts_reconciliation` ·
`mart_player_season_records` · `mart_stat_leaderboard` ·
`mart_team_acquisition_channels` · `mart_team_alltime` ·
`mart_team_matchup` · `mart_team_points_reconciliation` ·
`mart_team_season_standings` · `mart_team_slot_production`

> **Noticed while mapping, not part of the epic:**
> `mart_player_fpts_reconciliation` and `mart_team_points_reconciliation`
> are *diagnostics* — they grade the reconstruction against ground truth
> (MLB-62, MLB-63) and no consumer surface renders them. They sit in
> reporting next to models that feed real tabs. Whether "reporting"
> should split consumer surfaces from diagnostics is a separate, smaller
> question; flagging it rather than folding it in.

---

## 4. The violations — options, not decisions

Each group states the trade-off honestly. **None of these is a
recommendation.** Where an option is cheap and reversible I say so;
where it changes a contract I say that too.

### 4.1 Group A — identity read backward (edges #1, #2, #3)

Three models read `dim_player_identity` from a layer that names itself
upstream of core.

**Option A1 — Move `dim_player_identity` into the identity layer.**
- *For:* fixes all three edges structurally, with no SQL change to any
  reader. The only mechanical change is the file's directory and, if we
  rename, its prefix.
- *For:* makes the epic's layer list true rather than aspirational —
  identity genuinely sits above reconstruction.
- *Against:* the `dim_` prefix currently signals "contract you may rely
  on," and `dim_player_identity` **is** relied on by core facts
  (`fct_cbs_player_game_attribution`). Moving it out of `marts/core`
  without renaming leaves a `dim_` outside marts; renaming it breaks
  every `ref()` and the hosted-docs URL.
- *Against:* touches the model at the centre of MLB-81, whose gate Kyle
  cleared by eyeball. Any rename shows up in that lineage.

**Option A2 — Rename the layers to match the graph; move nothing.**
Accept that identity is a legitimate cross-cutting layer, rename
`intermediate/` to reflect its real contents, and declare
`dim_player_identity` a *shared upstream contract* rather than a core
dim.
- *For:* zero graph movement, zero rebuild risk, zero golden exposure.
- *For:* "a contract can be consumed by any layer below it" is a
  defensible rule, and it is what the code already does.
- *Against:* leaves a `dim_` in `marts/core` that three intermediates
  read, which is exactly the thing a reviewer flags. Solves the honesty
  problem in prose, not in structure.

**Option A3 — Split it: a thin `identity` resolver upstream, and a
`dim_player_identity` contract view over it in core.**
- *For:* both readerships get what they want. Reconstruction reads the
  resolver; consumers keep the dim at its current path and name.
- *For:* no consumer-facing rename at all.
- *Against:* one more node, and two names for one idea — the exact
  "pass-through with no logic in it" the dbt README already argues
  against for `mart_daily_roster_snapshot`.
- *Against:* the resolver is a `table` today; splitting means deciding
  which half is materialised.

### 4.2 Group B — core fact read backward (edge #4)

`int_cbs__player_daily:60` reads `fct_cbs_player_game_attribution`.

This is the ping-pong's hinge, and it is the hardest of the seven,
because the fact is doing real work its reader needs: attribution of a
priced player-game to the franchise that rostered the player that day.

**Option B1 — Move `fct_cbs_player_game_attribution` to reconstruction.**
- *For:* the ping-pong disappears; the chain becomes a straight descent.
- *For:* by subject matter it *is* reconstruction — it is the historic
  active lens, output of the walk-back.
- *Against:* it is a `fct_` with a documented grain that marts read
  directly (`mart_team_points_reconciliation`). Demoting a fact out of
  the contract layer means reporting reads reconstruction — trading one
  layer violation for a different one.

**Option B2 — Promote `int_cbs__player_daily` past it.**
Recognise the convergence layer as sitting *after* core-for-CBS, and let
convergence read core.
- *For:* no model moves; the rule becomes "convergence may read core,"
  which is already true of `int_player_daily`'s neighbours.
- *Against:* convergence then straddles the contract layer, and
  `int_player_daily` (its sibling) reads only staging + convergence. The
  two halves of one layer would sit at different depths.

**Option B3 — Leave it; declare it a documented exception.**
The dbt README already carries three "edges that look odd" with written
justifications, and that pattern has held up well.
- *For:* cheapest, and honest as long as it is written down.
- *Against:* the fourth exception is where "documented exception" starts
  reading as "we gave up." Worth a hard look at whether the layer model
  is wrong if it needs four carve-outs.

### 4.3 Group C — owner label read backward (edge #5)

`int_cbs__player_daily:136` reads `dim_team_owner` for a display label.

By the dbt README's own rule of thumb this is the textbook smell: it
skips layers to fetch *one field* a nearer layer already carries.

**Option C1 — Rewire to `int_cbs__team_owner_season`** (identity layer,
already upstream, already in the graph).
- *For:* smallest possible change; kills the edge outright.
- *Against:* must confirm the grain matches — `dim_team_owner` collapses
  co-owned teams to `"Name / Name"`, and if the reader depends on the
  collapsed form, the upstream model may not produce it identically.
  **This is a value-moving risk and must be proven under goldens.**

**Option C2 — Drop the label here; attach it downstream.**
- *For:* purest — display labels do not belong in a convergence feed at
  all.
- *Against:* every consumer of the column then has to join for it. Needs
  a survey of who reads it before it can be costed.

**Option C3 — Accept it as a dimension read.**
"A model may read a dim at any layer" is a coherent rule that also
absorbs edges #3 and #6.
- *For:* one rule retires three violations.
- *Against:* dims are in `marts/core`, so the rule amounts to "marts are
  not really a layer." That may be true and worth saying — but say it
  deliberately.

### 4.4 Group D — seed contract read backward (edge #6)

`stg_cbs__player_season_stats:88` joins `dim_stat`.

`dim_stat` is a **thin view over the `stat_classification` seed** with no
logic of its own. The staging model already reads the `cbs_stat_map`
seed two lines away.

**Option D1 — Read the seed directly**, as `stg_cbs__scoring_settings`
and `int_player_daily` already do.
- *For:* mechanically trivial, no grain change, and it makes the model
  consistent with its own neighbours.
- *For:* removes the violation with the best effort-to-payoff ratio of
  the seven.
- *Against:* the dbt README explicitly argues the *opposite* — that
  everything downstream of core should read the seed only through the
  dim. Doing this in staging is consistent with that (staging is not
  downstream of core), but the reasoning needs restating so the next
  reader does not "fix" it back.

**Option D2 — Leave it; declare seed-backed dims layer-free.**
- *For:* `dim_stat`, `dim_matchup_period` and `dim_roster_slot_counts`
  are thin seed/staging views; calling them layer-free is honest.
- *Against:* a reader still cannot tell which dims are thin without
  opening them.

### 4.5 Group E — the model that was never staging (edge #7)

`stg_cbs__team_owners` reads **no source**. It reads
`int_cbs__team_owner_season` and reshapes it into `stg_team_owners`'
grain so `dim_owner` → `dim_team_owner` serves both platforms.

This one is genuinely a **naming** bug rather than a wiring bug, which
makes it the cleanest candidate in the set.

**Option E1 — Rename to its role** (`int_cbs__team_owners_bridge`, or an
identity-layer name).
- *For:* fixes the violation with a rename; no logic changes.
- *For:* stops the file from lying about reading a source.
- *Against:* renaming a `stg_` model touches its `ref()` sites
  (`dim_owner`, `dim_team_owner`) and its schema YAML.

**Option E2 — Leave the name; document it as an adapter shim.**
- *For:* zero risk. The name does communicate "CBS-side twin of
  `stg_team_owners`," which is useful even if the prefix is wrong.
- *Against:* it is the single clearest counter-example to "only staging
  reads `source()`, and staging reads only sources" — a reviewer will
  find it in a minute.

### 4.6 Open call — do the identity contracts move? (no edge; design)

`dim_franchise`, `dim_franchise_season`, `dim_owner`, `dim_team_owner`
are identity by subject and contract by role. §3 leaves them in core.

- **Option F1 — leave all four in core.** Identity is a *processing*
  layer; its published contracts live in core with every other dim.
  Cleanest boundary rule; leaves the layer name slightly misleading.
- **Option F2 — move all four to identity.** Subject-matter coherence:
  everything about *who* is in one place. Largest blast radius of
  anything in this document — these four are read by marts and Python.
- **Option F3 — move none, and rename the layer** from "identity" to
  something naming the *work* (`resolution/`) rather than the subject, so
  the contracts staying in core reads as intended.

---

## 5. Exposures truth-up (MLB-158 Phase A, second bullet)

Not implemented tonight — editing `exposures.yml` changes the published
lineage, which is a real change rather than a draft. Recording the
findings so the edit is mechanical when it is taken up:

1. **Missing consumer.** [`output/generate_season_report.py`](../output/generate_season_report.py)
   is a production consumer with **no exposure entry**. Its warehouse
   reads need enumerating.
2. **ESPN/CBS not split.** The `league_almanac` exposure predates the CBS
   book. It declares 17 upstream nodes and does not enumerate the CBS
   side. Splitting it into `espn_almanac` / `cbs_almanac` would make both
   lineages legible.
3. **Documented exceptions belong in the declaration.** The
   `stg_scoring_settings` read (glossary points-per-unit) is already
   declared on the almanac exposure — the right pattern. Any
   staging/intermediate read that survives §4 should be declared the same
   way rather than left implicit.

Per Phase C and Kyle's drift-fatigue rule, **no test enforces any of
this yet**, and none should until the graph stops moving.

---

## 6. What this draft does not do

- **Decides nothing.** Every §4 group is options with trade-offs.
- **Moves no model, renames nothing, edits no YAML.** Zero graph change;
  `dbt parse` unaffected.
- **Costs nothing.** No option is estimated. Phase B rides MLB-10's port
  slices, and sizing should happen against that plan, not this document.
- **Does not touch MLB-130.** Franchise-identity-from-`ui_standings` is a
  named instance of this disease (`int_franchise_seasons` and
  `stg_cbs__ui_rosters` both resolve franchise identity through
  `stg_cbs__ui_standings`), but it has its own ticket and its own
  remedy. Noted, not absorbed.

## 7. Review checklist for Kyle

The four calls that unblock Phase B, roughly in dependency order:

1. **§4.1 — is `dim_player_identity` core or identity?** Everything else
   is easier once this is settled; it is 3 of the 7 edges.
2. **§4.2 — the ping-pong.** The only one with no cheap option.
3. **§4.6 — do the four identity contracts move?** Sets the blast radius
   for Phase B.
4. **§4.3 C1 — is the `dim_team_owner` → `int_cbs__team_owner_season`
   rewire grain-safe?** The one place in this document with genuine
   value-moving risk; needs a golden run, not a reading.

§4.4 (D1) and §4.5 (E1) look like the cheapest real wins and could ride
any port slice independently of the above.
