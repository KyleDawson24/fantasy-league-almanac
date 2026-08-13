# Release Notes -- v1.8.0

30 commits since v1.7.0.

**The engine runs locally, end to end.** A fresh clone with league credentials and no warehouse account of any kind now reaches rendered previews. That was the gate on 2.0's first keystone, and it is the whole reason this release exists.

The rest of the release is what walking that path turned up: eight ways a stranger's first run could die before producing anything, a lineup-slot fallback that was quietly deleting production, and a standings order that was provably not the one the platform uses.

Minor rather than major. The standings reorder and the podium marks move rendered values, but nothing needs a migration step and no existing install has to do anything.

---

## The no-warehouse path

Until now `extract.py` landed raw league data in Snowflake and nowhere else, so "build and render locally" was true and "run this without a warehouse" was not. The front door was still a cloud account.

`--raw-target local` writes parquet plus a manifest instead, into the directory `tools/load_parquet_to_duckdb.py` already reads. Nothing about the loader changed -- it still just loads what the manifest lists.

Measured on a genuinely fresh clone: a new venv from `requirements.txt`, a `.env` holding `LEAGUE_ID`/`ESPN_S2`/`SWID` and **no `SNOWFLAKE_*` key present anywhere in the run**, league config filled from the demo fixture.

| stage | result |
|---|---|
| extract → parquet | **23 tables** |
| parquet → DuckDB | **23 tables** |
| dbt | 18 seeds, **74 of 74 models** |
| render | **19 almanac tabs** |

Those are the numbers *the acceptance walk produced*, not today's totals. The project has grown to 19 seeds and 78 models since, because the settings and standings capture below added staging models and a dimension. The walk has not been repeated against the larger project, so the figures are reported as measured rather than restated.

Two findings from that walk are worth stating rather than smoothing over, because both are the difference between "it built" and "it built the right thing":

**Absent and empty are different answers.** 
An ESPN-only extract populates 6 of the contract's 23 tables. With the other 17 *missing*, the MLB-72 convergence layer reads CBS staging models directly, so `dim_owner` and `int_franchise_seasons` fail on a missing relation and drag a 27-model skip cone with them -- **12 of 41 selected models built**. With those same tables present but *empty*, the project builds **74/74 green**. So the local sink now gives every contract table a file, empty if this extract does not populate it.

**The contract is generated, not hand-written.** 
The RAW type contract is emitted from Snowflake and asserted against on both sides, and the local half runs with no credentials, no accounts and no network. The strongest test hands the writer's parquet to the real loader, unmocked, and inspects the resulting DuckDB: `RAW_JSON` arrives as JSON and is queryable by path, `NUMBER(38,0)` lands as `DECIMAL(38,0)`. That proves the output is accepted by the consumer that already existed, rather than proving two things I wrote agree with each other.

[QUICKSTART.md](QUICKSTART.md) acknowledges that a warehouse is no longer required, and the commands in it are the ones actually run, in that form. Two rough edges are documented rather than hidden: `dbt build` trips `assert_cbs_scoring_feed_matches_seed` on an ESPN-only install, because the CBS scoring feed is legitimately empty there, so the step says `dbt run` and says why. dbt needs Python 3.13. On 3.14 it dies at startup inside `mashumaro` (a serialization library in dbt's dependency chain) complaining about a field named `schema`. Since dbt projects _have_ a `schema:` setting, that looks like your configuration; no amount of editing it will help. It's the Python version, which is why that's now the first thing the requirement says.

## Eight crash paths a stranger's first run would have hit

Every one of these took down a whole run or a whole build, not the piece it belonged to. None were reachable in our sample leagues, which is exactly why they survived: they need a league shaped differently from the ones I'd built for.

**In the extract**

1. **A bye week killed the run.** In an odd-numbered league the wrapper leaves the absent side as the integer `0`, and reading `.owners` off it raised `AttributeError` before the first row was written. The bye is now *represented*  rather than dropped -- an explicit BYE side carrying a NULL `team_id`, because a sentinel integer collides the day a league really does number a team 0, and because the team on the bye still played a full slate that has to reach the marts.
2. **A league that never drafted could not build.** `DRAFT_PICKS` and `TRANSACTIONS` had their `CREATE TABLE` inside a conditional write, so the table was never created and `dbt run` failed resolving `source('raw', 'draft_picks')` -- a missing relation, not an empty read. Seven tables are now seeded up front.
3. **An unmapped lineup slot id** raised a bare `KeyError` and killed the extract with nothing to act on. It now fails loudly, naming the id, the period, and the ids the installed wrapper does know. Deliberately a failure and not a coercion -- guessing corrupts totals instead of stopping.

**In the CBS book**

4. **`max()` over an empty draft.** Not hypothetical: the draft-assembly seed's last row is 2026, so the first 2027 standings row makes a season with no picks behind it.
5. **`int(None)` on the all-time acquisition block** -- a first-year league makes both era aggregates NULL over an empty set.
6. **`seasons[0]` on the finishes matrix**, same cause. The head-to-head path guards every all-time block; this one guarded none. The tail of the same defect was cosmetic rather than fatal: first-year era banners rendering the literal strings `"None-2026"` and `"= None gameplay days"`.
7. **A non-numeric draft period label** took down the entire CBS capture catalog -- a bare `int()` on a dropdown label, so a "Supplemental" option lost every year's drafts rather than its own.

**In the ESPN book**

8. **The standings side table ran off the end** at 30 teams over a 23-period season (`IndexError`), because it wrote into rows the left-hand blocks had already made.

Separately, **a bye-week team's production vanished entirely** from the weekly fact -- both arms of the matchup self-join were inner joins, so a team with no opponent produced no row at all. A silent hole rather than a visible gap, and `mart_league_weekly_benchmarks` documented the opposite in its own header. `result` is now NULL for a bye rather than `'T'`, because a bye is not a tie.

## The lineup-slot vocabulary is a seed now

`stg_box_scores` classified a lineup slot with a closed list ending in `else 'hitting'`, and that `else` was the bug. `lineup_slot_category` is used as a **stat filter**, so an unrecognised slot did two-sided damage: a hitter in one counted as active starter production, and a pitcher in one had his pitching stats **deleted outright** -- neither active nor inactive, just gone. Slots that fell through included `IF`, `UTIL`, and the empty string the wrapper produces for a slot id it cannot map.

The vocabulary already existed, correct and complete, as an inline `VALUES` block one model over. It is now a seed -- lift and shift, identical nineteen rows, identical model output.

The fallback deliberately stays `'hitting'` rather than becoming null: a category matching no stat category deletes the player's stats, so making the bad case louder at *value* time makes it quieter in the *output*. **The alarm moved to build time instead.** A new test asserts that every slot observed in player-day data, and every slot the settings dictionary knows, has a seed row -- demonstrated by deleting the `UTIL` row and watching the build fail naming it. The remediation is one row in a CSV.

Nothing moved. 
194,946 rows across 17 `(lineup_slot, lineup_slot_category)` pairs before and after, every pair identical, both almanac goldens unmoved.

## ESPN's own settings and standings are captured
Every run was already building a league object and pulling `mSettings`, and both carried far more than the two blocks being read. `scheduleSettings`, `draftSettings`, `acquisitionSettings` and `tradeSettings` were fetched and dropped on the floor; so were divisions, records, playoff seeds and final ranks.

**No extra API call.** ESPN's `view` parameter repeats rather than replaces, so `mSettings` and `mTeam` come back in one document -- verified on two seasons that the settings block is byte-identical to what a `mSettings`-only request returns, which is what makes adding a view safe for the caller already reading it.

One RAW table per block rather than one `SETTINGS` table, because troubleshooting roster slots and troubleshooting the playoff bracket are different errands, and RAW is where you go when you do not yet know which one you are on.

2025 backfilled cleanly: all sixteen teams read back with seed and final rank as contiguous 1..16 permutations. Two payload traps are handled at the staging seam rather than left downstream -- division ids are **not contiguous** (a league that drops divisions keeps the surviving ids), and one division name really does carry a trailing space, which is non-null and unique and so would fail much later as a duplicate-looking label.

## The visible change: standings in the platform's own order

The almanac ordered teams by wins, then ties, then points. **ESPN does not seed that way.** It seeds division winners ahead of the field and orders the rest by record, and no sort over wins or points recovers that.

The two-division season is the counterexample: both division leaders take seeds 1 and 2, and the best record among teams leading nothing -- two games better than the second division's leader -- seeds only **3**. A flat record sort swaps those. The remaining eleven seeds match record order exactly, near-proving that it is a rule rather than a coincidence, and the four-division season reproduces it with seeds 1-4 being precisely the four leaders.

The fix is not a better sort. It is reading the number the platform already computed.
**Two sites carried the wrong rule**, not the one that was flagged -- the second drives the Detailed Standings row order and its Rank column, so fixing only the first would have left the headline table wrong while the small table beside it became right.

**The podium is marked on both books.** 🏆 champion, 🥈 runner-up, 🥉 third.

On the head-to-head book these key on the **post-playoff finish**, not the seed, because the podium is settled in the bracket and the two genuinely disagree: the last closed season was won from the **7 seed** while the **1 seed** finished second. 
A medal placed off the seed would decorate the wrong teams. That the third-place mark is a real third-place game was confirmed by walking the bracket rather than assumed -- the two semi-final losers play each other in the final week. The points league has no playoffs, so its second and third are the season standings, and its ordering is untouched.

The champion trophy keeps its own derivation -- won every playoff week -- and outranks a disagreeing final rank, so a trophy can never appear that the Titles column beside it would not count.

Two details that only exist because the fix was checked against a rendered book rather than a test: the medals take **the colour the gradient would have given their own rank**, so a medal joins the finish grading instead of punching a flat swatch through it; and a header sharing the navy banner row had been rendering black on dark, because setting `textFormat` replaces it wholesale and dropped the white.

Why the two can disagree, and why that is stated on the surface rather than resolved: the tables read the platform's seed, but the Rank by Week chart is reconstructed from weekly results and **has to be** -- ESPN keeps no intra-season standings snapshots, so a per-week seed does not exist at any price. The tab now says so. [docs/decisions/STANDINGS_ORDER_AND_THE_RANK_CHART.md](docs/decisions/STANDINGS_ORDER_AND_THE_RANK_CHART.md) records the whole split, including the config setting it is really waiting for -- whether standings follow the platform's seeding or a plain record sort is a *league preference*, not a fact.

Standings capture now runs on **every** extract rather than riding the opt-in settings flag. The two arrive on one response and have opposite refresh needs: settings change once a season, standings change weekly. Left behind the shared flag, a box-score pull advanced the W-L column while the row order stayed frozen at the last settings capture -- so the table disagreed with itself and read as a rendering bug.

## Determinism and truth-in-documentation
**A tie in the wasted-points list was re-rolling the golden with nothing underneath it
having moved.** The sort was on a bare value, and equal values have no guaranteed order. It compounds an older float lesson: two values that both *display* as 28.6 differ in the fifteenth decimal, so ordering on the raw value separates them strictly and a name tiebreaker never fires. The rounded value now sorts first, then the name the surface already prints -- so a golden can never move for a reason a reader cannot see. Cosmetic today at ranks 4 and 5 against `LIMIT 5`; this league already contains a three-way tie one row from silently dropping a player out of a shipped callout.

**A guard was correct and its documentation named the wrong field.** Every stated rationale for the settled-history guard described a per-day stamp that nothing has read since an earlier flip -- so a reader who did the right thing, and checked whether anything consumed it, correctly concluded the guard protected nothing. Three separate times. It is now explained by what it actually protects, and the refusal message carries the argument itself. **A guard's documented rationale is a load-bearing part of the guard**, because the next reader audits the rationale rather than the behaviour.

The public face got another pass for the same reason: a memory-headroom bracket filled in from the measured series, and a claim that two surfaces had converged in lockstep corrected to name the one that has and the one that has not.

## Privacy
Six commits took real identifiers out of tracked files: the real ESPN league id from the almanac tests and an archived journal, real franchise abbrevs from test fixtures, real team and owner names from a bye-week fixture, and real names from code comments describing data quirks.

The league id is the sharpest of these -- with the public ESPN endpoints it identifies the league directly, and the PII guard has never scanned numeric identifiers, so it was never going to catch it. The abbrev class was invisible to the guard for a different reason: it sources its strings from the anonymization map, and every abbrev in that map is CBS-side, so the entire ESPN franchise-abbrev class was structurally unseeable.

Two rules came out of it, and both are now house rules. **A synthetic fixture is synthetic all the way down, names included** -- the guard is the last line of defence, not the first, and it matches a fixed string list, so a real identity it has not been told about goes straight through. And **documentation describes the shape of a real-data quirk, never the instance.**

---

## Limits
**CBS bring-your-own is not in this release.** The no-warehouse path is ESPN only. 
CBS capture still needs the browser-credential route, and that is what stands between this and the 2.0 goal of *"an ESPN **or CBS** league"*.

**A league in its first season sees all-time surfaces duplicating the current one.**
They build and render rather than crashing -- which is what this release fixed -- but with one season of history, "all-time" and "this season" are the same numbers shown twice. Worth knowing, one day soon we'll hopefully add logic to replace the all-time versions with something more interesting, or even just prevent them from rendering in <=1-season old leagues to reduce clutter. But, for now, they render like any other league and will repeat themselves quite often.

**The rendered output is still files on disk plus a Sheet you have configured yourself.** 
The 2.0 goal is a workbook in your own Drive with sharing already set, because a league almanac the league cannot open is a demo rather than the intended product. That is MLB-209 and it is not this release.

**The head-to-head standings order now depends on a live platform value.** 
It used to be computed from warehouse data and moved only when new games loaded. It is the platform's own answer now, which is the point -- but that answer can change with no game played, as it did during this release's own verification when three tied teams reshuffled on a tiebreak. Correct, and worth expecting.

## What 2.0 is for

Unchanged from v1.7.0, and one keystone closer:

> **A stranger with an ESPN or CBS league enters some credentials, runs some things,
> and gets an almanac their league can open.**

**MLB-208 -- extract writes RAW locally -- is what this release closes**, for ESPN.
**MLB-209 -- the journey ends in a shareable workbook** -- is the one still open, and
it is a credential story rather than a code hole.

Version numbers are promises here. 2.0 ships when that journey is real for a stranger
on either platform, which is exactly why this cut is 1.8.0.
