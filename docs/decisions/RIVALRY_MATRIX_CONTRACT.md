# The Rivalry Matrix contract (MLB-229)

What the matrix means, which rulings produced it, and what is left.

Supersedes `HEAD_TO_HEAD_RIVALS_CONTRACT.md` (deleted), which described a
provisional first pass. Two of its claims were wrong and are corrected here:
it counted a matchup as complete once both running scores existed, and it
reported season-points support as blocked on unresolved assumptions.

**ONE MATRIX, SELECTED BY FORMAT.** An earlier revision of this document
described two grids stacked on every league's tab. That was an invented product
change and is corrected below: the matrix is one table whose definition of "a
game" follows the league's format.

---

## 1. Identity: what counts as one team

Kyle's ruling, in the order it applies:

1. **Platform ids identify source records.** They are never rewritten. Every
   fact keeps the team id the platform served.
2. **An explicitly configured canonical name IS the aggregation identity.**
   Where the league has written one in `franchise_lineage`, that name is the
   team.
3. **A configured name collapses everything that carries it** — different
   platform ids, and different `canonical_franchise_id`s. Two franchises the
   lineage never linked are one team if the league named them the same thing.
4. **Historical names and re-minted ids roll into it.** A configured name
   written against a re-minted id names the whole lineage, earliest era
   included — it belongs to the franchise, not to the row it was typed on.
5. **Without a configured name, the identity is the canonical franchise id**
   and the best observed name is a display label hung on it.
6. **Two fallback identities never merge on a matching observed name.**
   Observation is a coincidence; configuration is a statement.

7. **A season-scoped lineage row may configure a name for that season
   alone**, taking precedence over the franchise-level answer without
   rewriting any other season. The seed schema has always allowed it; the
   season dim used to read only the id from such a row, so it silently did
   nothing.

The asymmetry between (3) and (6) is the whole ruling, and it is why
`dim_franchise.canonical_name` cannot be the group-by key: it coalesces the
configured and observed cases into one string, so grouping on it would merge
(6) and lose (3)'s provenance.

### What changed to support it

- `dim_franchise` gained `configured_name` / `configured_abbrev` /
  `has_configured_name`. These are **lineage-wide** — resolved from the
  latest-observed member that carries an override — while `canonical_name`
  keeps its per-id resolution untouched. `canonical_name` is a rendered label
  with goldens behind it; widening it would have moved output under a ticket
  that is not about display.
- `dim_franchise_season` carries the same three through.
- `dim_franchise_identity` (new, season-grain) applies the rule.
  `identity_key` is prefix-tagged — `name:Bent Spokes`, `fid:14` — so the two
  kinds share one column and a league that names a team `13` cannot collide
  with franchise 13.

Matching is **exact after trimming**: no case folding, no punctuation
smoothing. A league that wrote two different strings meant two different
things.

### Accidental collisions

`assert_configured_name_has_no_active_collision` **warns** — per season — when
two teams that were both playing share one configured name. The rule still
aggregates them, because it has to mean the same thing everywhere; the warning
is how the league notices a seed typo. Warn rather than error: the rule
produces a defined answer, nothing is corrupt, and a build that refuses to
finish over a display-seed typo is a gate people learn to skip.

The old fixture treated two ids both named "Bent Spokes" as teams that must
stay apart. Under this ruling that is exactly backwards, and the test now
asserts the collapse.

---

## 2. Completion: what counts as played

**A matchup counts when its period is CLOSED**, it has an opponent, the two
sides are different platform teams, and both carry a platform score.

The provisional pass had no closure gate and counted a matchup as soon as both
scores were non-null — which is true on a Tuesday. `int_matchup_period_evidence.is_closed`
is the real signal, and it already existed: a period strictly below the
current one, or the final period of a season ESPN has finished with (proven
shape, membership reaching `finalScoringPeriod`).

**The gate has two layers and fails closed in both.**

1. A season the capture **reached** must prove each period closed,
   individually. Nothing else rescues a period the pointer has not passed.
2. A season the capture **never reached** is retained only where the season is
   independently proven finished (`int_league_season_closure`). An unproven
   season mints nothing.

Layer 2 is a correction, not a refinement. Treating "no capture" as
"historical, keep everything" meant a league that had never run the schedule
extract counted its live season's running scores as results -- the same bug the
closure gate exists to remove, reintroduced one level up. Absence of evidence
is not evidence of completion.

**Capture presence is read from `stg_matchup_schedule`, not from the derived
period evidence.** A capture that exists but is malformed produces zero
evidence rows, and a gate keying on the evidence would read that as "never
captured" and fail *open* on precisely the season whose payload could not be
understood.

A league whose latest season has no capture therefore shows no results for it.
That is correct and silent, so `assert_live_season_has_schedule_capture` warns
with the remedy -- run the schedule extract.

Both scores are still required, for a different reason: the fact derives
`result` as `W` / `L` / else `'T'`, so a NULL score would enter a rivalry
record as a **tie**. It also makes the filter symmetric — one team's
`platform_points` is the other's `opponent_points` — which is where
reciprocity comes from.

**Playoffs stay in.** A standings is a regular-season object; a rivalry is not.
**Abnormal-length periods stay in.** `is_record_eligible` gates per-week
extremes, and a head-to-head result is not an extreme.

---

## 3. Season points

One completed season = one win, loss or tie on **raw total points**.

- **Authoritative platform totals only** (`int_franchise_season_points`), never
  our recomputed lens: ESPN's `stg_team_standings.platform_points`, and CBS's
  parsed final standings `stg_cbs__ui_standings.total_points`.
  `stg_cbs__standings` is deliberately not a third arm — it carries the live
  season only, with no completion evidence, so every row it could contribute
  would be filtered out again. It becomes the right seam the day CBS gains a
  completion signal.
- **No margin weighting.** Outscoring a team by 1 and by 1,000 are both one
  win.
- **No normalisation for periods played.** A team that played fewer weeks
  scored fewer points, and that is part of what happened.
- **Only seasons both teams played.** The pairwise join is an INNER join on
  season, so a season one side sat out produces no verdict in either
  direction — an absent team neither outscored anyone nor was outscored. Every
  completed season both played is compared; none is otherwise excluded.
- **An identity's platform ids are summed before the comparison**, so a team
  that fielded two ids in one season is compared once, on its whole output.

### Completeness

Not one rule, and `completion_evidence` records which applied:

`int_league_season_closure` owns this for **both** ledgers -- two answers to
one question drift, and the first version of each drifted toward yes.
Precedence:

| Evidence | Meaning |
| --- | --- |
| `schedule_capture` | The capture reached this season and it decides, both ways. Stale final ranks must not resurrect a season the platform says is still being played. |
| `delivered_final_rank` | No capture, but the platform published final ranks. ESPN serves `rankCalculatedFinal = 0` for an unfinished season, so a non-null rank is proof -- and it is consulted **before** supersession, so the latest loaded season is no longer withheld merely because an optional extract has not run. |
| `parsed_final_standings` | Parsed year-end standings, final by construction |
| `superseded_season` | The league has played a later season |
| `unproven` | Nothing above answered -- and that means **no** |

The supersession fallback is a statement about the league's own timeline
rather than a guess about the calendar, which is what makes it safe for a
historical season and still refuses the live one.

---

## 4. Activity

**Active is determined from current platform ids in the latest team capture**
(`int_franchise_current_teams`: `stg_team_standings` / `stg_cbs__standings` at
each league's latest season), resolved through the identity rule and
deduplicated — two live ids sharing one configured name are one axis.

**Activity applies to the AXES, never to the fact aggregation.** An active team
keeps every result its former ids and names earned; a folded team keeps its
games and loses its column. The same ledger therefore serves a current-teams
matrix, an all-time one, or anything between, by changing which axes are asked
for.

The standings feeds are used rather than roster snapshots because the question
is which *teams* exist — reaching a roster would depend on the player chain to
answer it.

---

## 5. The rendered matrix

`mart_franchise_rivalry` is **long**: one row per ordered pair of identities.
The matrix is a render, not a grain — a wide table needs a column per team, so
its schema would move whenever a league gained or lost one.

**The format selects the ledger** (`dim_league_format`, by data presence --
never by platform name, per the house rule):

| Format | Signal | Ledger shown |
| --- | --- | --- |
| `h2h` | matchup pairings exist | completed matchups |
| `points` | delivered period standings exist | completed seasons on total points |
| `unknown` | neither yet | none -- a matrix whose meaning cannot be stated is worse than no matrix |

Rendering both everywhere would show every H2H league a table nobody asked for
and every points league a grid of 0-0 that means "this league does not work
that way" -- which is not what 0-0 says anywhere else on the tab.

### Three states, not two

`rivalry_matrix_grid` densifies over the active axes, and there are now THREE
distinct appearances a cell region can have. Two of them are cells; the third
is the absence of cells, and conflating it with either is the failure this
section exists to prevent.

| State | Appearance | Means |
| --- | --- | --- |
| Diagonal | blank cell | a team has no record against itself |
| Never met | `0-0` | they have played nobody; **something is known and it is nil** |
| No evidence | **no grid at all**, plus a conspicuous notice | **nothing is known**; nobody can prove any result yet |

The third is a release requirement, not polish. The ledger fails closed, so a
league whose history nobody can prove yields no rows — and densifying zero rows
produces a full square of `0-0`, which is a *claim*: "these teams have played
and never beaten each other". For a league whose schedule capture has never run
that claim is false, and it is pixel-identical to the honest `0-0`.

`mart_franchise_rivalry_axes.has_rivalry_evidence` separates them at the source:
true when at least one season is admissible (proven finished, or carrying a
closed period). League-grain deliberately — "we cannot prove any result" is a
property of the capture state, and asking it per pair would make an expansion
team's genuine `0-0` look like missing evidence.

The unavailable state carries **no cells at all** — no grid, no header — so
there is nothing a reader could mistake for a record, and it says out loud that
nothing is known rather than that nobody won. The banner stays, so a reader who
has heard the matrix exists finds it and learns why it is empty rather than
finding nothing and wondering whether the publish broke. The renderer's own
default is fail-closed too: an axes row missing the column takes the
unavailable path.

The two cell states remain deliberately different:

- **The diagonal is blank.** A team has no record against itself and there is
  no honest number for that cell.
- **Two active teams that never met read 0-0.** That is a fact about the
  league — an expansion team, an unlucky rotation — and blanking it would hide
  it.

Collapsing those into one appearance loses the distinction, which is why the
mart emits no diagonal row at all: the renderer cannot get it wrong by
accident.

One grid at the bottom of **Advanced Standings**, in **both books**: a
standings answers "who is ahead", and "against whom" is the next question.

`rivalry_matrix_grid` is the shared contract -- which ledger, how a cell reads,
what the diagonal does, what the explainer promises. Only the LAYOUT is
per-book: the ESPN builder returns rows for the write layer to paint and gives
the ledger its own label row; the CBS builder accumulates rows and format
ranges together and rides the ledger name on the section banner as a scope
caption, this tab's idiom. Two idioms, one product.

Wired on **both ESPN workbook paths** -- preview/generator and publish --
because a block that renders in preview and silently vanishes from the
published sheet is a failure this tab has already had once.

Row labels disambiguate with the abbrev when two axes share a display name,
which is the supported (6) case above. Found in visual QA.

---

## 6. Where things live

| Layer | Model |
| --- | --- |
| Identity | `dim_franchise_identity` (+ provenance on `dim_franchise`, `dim_franchise_season`) |
| Completion | `int_league_season_closure` |
| Format | `dim_league_format` |
| Season totals | `int_franchise_season_points` |
| Current teams | `int_franchise_current_teams` |
| Ledger | `mart_franchise_rivalry` |
| Axes | `mart_franchise_rivalry_axes` |
| Render (shared) | `almanac_logic.rivalry_matrix_grid` |
| Render (ESPN) | `almanac_logic.build_rivalry_matrix_rows` -> Advanced Standings |
| Render (CBS) | `cbs_almanac_sheets.build_standings_rows` -> Advanced Standings |

---

## 7. Known limits

- **Goldens are not re-anchored.** The byte-diff harness is `warehouse`-marked
  and needs a live Snowflake connection, which the MLB-229 worktree does not
  have. Advanced Standings gains rows, so the goldens WILL move and must be
  re-anchored under review from an environment with warehouse access, per the
  CLAUDE.md rule.
- **The PII guard cannot run non-degraded here.** The private anonymization map
  is local to the main checkout.
- **Both golden sets will move** and neither is re-anchored here, by
  instruction — see §8 for the exact expected movement, recorded for the single
  consolidated v1.9 re-anchor.


---

## 8. Expected golden movement (recorded, NOT applied)

Neither golden set is regenerated on this branch. This is what a reviewer
should expect to see when the consolidated v1.9 re-anchor runs, so that a
diff matching this list is a confirmation and a diff exceeding it is a
finding.

### Scope

Both byte-diff harnesses are `warehouse`-marked and need a live Snowflake
connection, which this worktree does not have. Nothing here was measured
against real league data; it is derived from what the code appends and from
the fixture renders in `tests/`.

### ESPN almanac — Advanced Standings tab only

Appended at the BOTTOM of the tab, after the Roster Affinity block. Nothing
above it moves: no existing row changes content, and no existing row changes
index, because the block is purely additive at the end.

Expected added rows, in order:

1. one blank spacer row
2. the `Rivalry Matrix` banner row (navy band, width unified with the tab)
3. one explainer row (italic house token)
4. one blank row
5. the ledger label row — `Head-to-Head Matchups` for this league
6. one header row: `Team` followed by one abbrev per ACTIVE identity
7. one row per active identity: display name followed by one cell per identity

So the tab grows by `6 + N` rows, where `N` is the number of active team
identities. Grid width is `N + 1` columns.

Cell content: `W-L` or `W-L-T`, blank on the diagonal. Every cell is a STRING
written under `value_input_option='RAW'` — no cell here should ever be parsed
as a date or a number.

No other tab changes. Records, Team Weeks, Home, Draft, Trades and the team
tabs are untouched by this branch.

### CBS almanac — Advanced Standings tab only

Same shape, one layout difference: the ledger name rides the banner row as a
scope caption at column B instead of taking its own row. That drops BOTH the
ESPN block's separate ledger-label row and the blank row above it, so the
fixed part is four rows rather than six and the block is `4 + N` rows:

1. one blank spacer row
2. the `Rivalry Matrix` banner row, with `Season Points` in column B
3. one explainer row
4. one header row
5. one row per active identity

Four fixed rows, then N. For this league's 16 active identities that is 20
rows, and the grid is `N + 1` = 17 columns wide.

For this league the ledger is `Season Points`, not head-to-head — there are no
matchups in a points league to have a head-to-head record about.

### What would NOT be expected, and should be treated as a finding

- Any change above the Rivalry Matrix block on either tab.
- Both ledgers appearing on one tab (the format dispatch has regressed).
- A `0-0` cell in a league with no capture (the evidence gate has regressed).
- A grid rendered with no `Team` header row, or a header with a different
  count of columns from the number of data rows.
- Any change to a tab other than Advanced Standings.

### Also unmeasured here

The strict PII guard cannot run non-degraded in this worktree — the private
anonymization map and salt are local to the main checkout, and were
deliberately not copied here. Run it during main integration.
