# The head-to-head rivals contract (MLB-229 rung 1)

What `mart_franchise_head_to_head` promises, what it deliberately does not
answer, and the two questions that have to be Kyle's before rung 2 renders
anything.

The model is the reusable data contract only. No workbook tab, no renderer, no
golden, no live extract touched it.

---

## 1. Source facts

| Used | Why this one |
| --- | --- |
| `mart_team_matchup` | The established matchup-grain surface, and the one `mart_team_season_standings` already rolls W-L-T out of. Reaching past it to `fct_team_weekly_active_performance` would re-derive its self-join for nothing. |
| `dim_franchise_season` | Season-aware lineage resolution. The season-grain dim, not the franchise-grain one -- see §3. |
| `dim_franchise` | Display labels, read from the canonical anchor row. |

`mart_team_matchup` descends from `fct_team_weekly_active_performance`, whose
`platform_points` is ESPN's authoritative team total -- slot-aware and
inclusive of commissioner adjustments -- and is the lens `result` is derived
from. The ledger uses that same lens for points for/against so a franchise can
never show more points than it conceded while holding a losing record.

Nothing else is read. No seed beyond `franchise_lineage` (through the dims), no
roster capture, no standings feed.

---

## 2. What counts as a completed meeting

A meeting is counted when it has an opponent, the two sides are different
platform teams, and **both sides carry a platform score**.

The third condition is load-bearing rather than defensive. The fact derives
`result` as `W` / `L` / else `'T'`, so a NULL score on either side makes both
comparisons NULL and falls through to the else branch -- an unscored week would
enter a rivalry record as a **tie**. Requiring both scores is also what makes
the filter symmetric: one team's `platform_points` is the other's
`opponent_points`, so a pair is either kept from both directions or dropped
from both, and reciprocity follows from that rather than from a separate rule.

**This is the strongest completion statement the committed facts support.**
There is no season- or period-completion flag in the warehouse to lean on:
`int_matchup_season_derivation` answers period *shape*, and
`dim_matchup_period.is_record_eligible` answers period *abnormality*. Neither
says "this game is over." So a matchup currently in flight is counted, with
provisional numbers that settle as the week does -- exactly how
`mart_team_matchup` and `mart_team_season_standings` already behave. A
consumer wanting only finished weeks filters upstream.

### Scope choices, all deliberate and all reversible

| Choice | Reasoning |
| --- | --- |
| Playoff meetings **in** | A standings is a regular-season object and rightly excludes them; a rivalry is not. Two franchises that met in a final met. `is_playoff` is on the fact, so the standings reading is one predicate away. |
| Abnormal-length weeks **in** | `is_record_eligible` gates the record book, where an odd-length week distorts a per-week extreme. A head-to-head result is not an extreme -- a win in a 10-day opening week is a win. |
| Holding pen **out**, both sides, before aggregation | The pen is not a franchise (MLB-115) and every team-grain aggregation fences it out. A season-scoped lineage row can park a real team-season on it; excluding after aggregation would leave the opponent holding a win nobody lost. |
| Byes **out** | The fact's own contract: `result` is NULL for a bye. |
| Self-matchups **out**, after resolution | Two platform ids collapsing onto one canonical franchise in one season is invisible to a raw `team_id <> opponent_id` check. |

---

## 3. Identity

Matchup facts keep their real platform franchise and team ids. Nothing is
rewritten upstream; resolution happens at read time.

Each team-season resolves through `dim_franchise_season`, keyed on
`(league_key, franchise_id, season_year)`. The season-grain dim is required,
not preferred: `dim_franchise` answers "which franchise is this id?" once for
all time, so a season-scoped lineage row applied there would rewrite a
franchise across its whole history. The parked-season case is exactly what the
season grain exists for.

Aggregation keys on `league_key + canonical_franchise_id` and never on name
text. Display resolves to the configured `canonical_name` when the lineage seed
gives one, else the resolved franchise's latest observed platform name -- both
already inside `dim_franchise.canonical_name`, read rather than re-derived so
the two cannot drift.

Consequences pinned by tests: a re-minted id is one rivalry across both eras; a
renamed franchise shows its current name; two unrelated franchises sharing a
display name exactly stay two rows, including when they play each other.

---

## 4. "Still active" -- NOT RULED ON

The ticket asked for the project-consistent definition, or the exact choices if
none is authoritative. **None is authoritative.** The mart therefore carries no
activity flag and filters nothing: every franchise that has completed a meeting
is in it, defunct or not.

What exists today:

| Where | Definition | Why it cannot be lifted |
| --- | --- | --- |
| `output/cbs_almanac_sheets.py` (`get_active_franchises`, and the stated rule "a canonical franchise is active if ANY of its ids is on the current roster") | Present in `stg_cbs__rosters` at the current roster capture date | CBS-only, renderer-side, and keyed to a live roster capture |
| `output/almanac_data.py` (`get_team_roster_history_stats`) | Present in `mart_daily_roster_snapshot` at the latest scoring period of the current season | A different mechanism reaching a similar answer; ESPN-only, also renderer-side, also a live capture |
| `int_franchise_registry.last_observed_season` | Latest season a franchise was seen playing | Explicitly documented as **display recency only** -- "identity anchoring stays the lineage seed's job." Borrowing it for activity would give it a second meaning it was written to refuse |

So there are two implementations that agree in spirit, disagree in mechanism,
live in the renderer rather than the warehouse, and both depend on roster
captures this ticket's scope excludes -- and a third signal that exists but has
been disclaimed for this purpose in writing.

### The exact choices, for Kyle

1. **Roster presence (what the renderers do).** Active = the franchise has a
   team in the current season's latest roster capture. Truest to today's
   behaviour; makes a historical mart depend on a live capture, and gives no
   answer at all for a league between seasons.
2. **Last-observed season (what the data already knows).** Active = the
   franchise played the league's most recent season. No new source, no capture
   dependency, and it works for any league on any platform -- but it overloads
   `last_observed_season`, which was deliberately scoped to display.
3. **A recency window.** Active = played within the last N seasons. Kinder to a
   franchise that skipped a year; needs N chosen and defended.
4. **No such thing.** The matrix shows every franchise that ever played and
   orders by something else (meetings, recency, name). No ruling needed, and
   the mart already supports it.

`first_meeting_season` and `last_meeting_season` are on the mart so whichever
of these wins can be applied by the consumer without changing the grain.

---

## 5. Season points -- TRACED, DEFERRED TO RUNG 2

Whether the existing facts support a season-level pairwise comparison cleanly.

**They support it structurally.** `fct_team_season_performance` is
`(league_key, season_year, team_id)`, regular-season only, and carries
`calculated_points` -- format-agnostic, populated for any league whose players
are attributed to teams, with no matchups needed. Resolving its `team_id`
through `dim_franchise_season` is the same join the H2H model already makes. A
within-season pairwise comparison is a self-join over that.

**It is not clean.** Four decisions stand between the fact and a defensible
win/loss/tie, and each is an assumption, not a lookup:

1. **Which lens.** `calculated_points` is the only measure present for every
   format; `platform_points` is the authoritative total but NULL for points
   leagues. Using calculated everywhere makes the season ledger disagree with
   the platform's own totals for an H2H league; coalescing mixes lenses across
   leagues in one column.
2. **Unequal exposure.** `periods_played` is on the fact and franchises differ
   -- a mid-season joiner, a shortened season. Comparing raw totals rules
   against a franchise for playing fewer weeks. Per-period normalisation is a
   different measure with a different meaning, and picking one is a product
   call.
3. **One franchise, two ids, one season.** The H2H model treats this as a
   self-matchup and drops it. At season grain the same collapse means two rows
   to combine -- sum them, or refuse the season? Summing invents a team that
   never fielded a lineup; refusing loses a season.
4. **Season completeness.** Same gap as §2, worse: an in-flight season's
   pairwise result flips as the weeks land, and a season-grain ledger reads as
   settled history in a way a weekly one does not.

That is new format abstraction on top of unproven assumptions, so it is not
being built here and it does not block the H2H foundation.

### The precise extension, for rung 2

A model at `(league_key, season_year, row_canonical_franchise_id,
opponent_canonical_franchise_id)` -- season kept in the grain so the pairwise
verdict stays auditable against the season it came from -- built by joining
`fct_team_season_performance` to itself within `(league_key, season_year)`,
resolving both sides through `dim_franchise_season`, and applying the same
holding-pen, self-pair and reciprocity rules as the H2H model. Its aggregate
rolls up to the H2H grain, so the two can sit side by side in one matrix cell.

It needs, before it is written: the lens (1), the exposure rule (2), the
collapse rule (3), and whether provisional seasons count (4).

---

## 6. Not in scope, on purpose

No workbook tab, no `generate_almanac_sheet.py` change, no golden regenerated,
no public doc rewritten, no live extract. Every test builds against synthetic
fixtures in a throwaway DuckDB under pytest's temp root -- no private league
config, no warehouse, no ESPN request, no Google account.
