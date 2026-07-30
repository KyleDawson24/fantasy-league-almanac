# Fantasy League Almanac


> Two fantasy baseball leagues, a quarter-century of history, one pipeline. It reconstructs seasons nobody recorded, re-prices more than a million individual player-game performances under one rulebook so eras can be compared honestly, and ships the result as a weekly recap and a browsable league almanac.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![dbt](https://img.shields.io/badge/dbt-1.11-orange)](https://www.getdbt.com/)
[![Snowflake](https://img.shields.io/badge/warehouse-Snowflake-29B5E8)](https://www.snowflake.com/)
[![Python](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)

Treats your fantasy league like it was a real league. Real product, real user base, real toy for real fantasy baseball nerds, and secondarily a deliberate analytics-engineering portfolio piece. Jump to [What this demonstrates](#what-this-demonstrates) if you are here to evaluate the engineering.

![The Advanced Standings tab: a 2026 rank-by-period chart above a 2001-2026 season-finish matrix](docs/img/cbs-advanced-standings.png)

**What you are looking at:** The matrix under that chart is twenty-six seasons of one league, 2001 through 2026, in a single grid. Stitching it together is the hard part. Franchises get renamed, handed to new owners, and re-issued fresh platform ids, so one continuous team can surface under three names and two identities across the run.

Resolving that lineage is what lets a single row credit the Hardball Hackers with five titles and a 4.7 average finish over a quarter-century, instead of scattering them across three unrelated rows.

The honesty note is the other half. Only 2026 was captured live. For our ESPN league, 2025 data still existed at full fidelity. For CBS, we needed some clever reconstruction.

2004-2020 is estimated from start-share rates and runs 5-13% low, always under rather than over. (Actual sit/start data is not available from that era so is estimated by site-wide numbers. It is a foreseeable selection bias that the league with 25 years of history made, on average, better start/sit decisions than the average random CBS subscriber)

2021-2025 is reconstructed day by day from the transaction log (which for this era _does_ include sit/start transactions).

Every era's provenance ships as a visible disclosure, not a footnote; the Home tab's Stat Sources table and every team page state plainly which years are captured live, reconstructed day-by-day, or estimated, and what share of the record falls into each bucket.

---

## The Big Picture

Every Sunday, one command turns a week of fantasy baseball into a formatted recap: best and worst matchups with their top contributors, the wasted-performance callouts, and any records broken or tied.

That was the original product, built to automate a tedious manual process for my personal league.
It has since grown into a cross-platform data model that produces customized, browsable "Almanacs" containing entire league histories.

The same transform layer now serves **two leagues on two different platforms with two different formats**: a head-to-head league where teams play each other weekly, and a season-long points league whose history runs back to **2001**.

Neither platform's website will tell you what its own rosters looked like fifteen years ago. So the pipeline rebuilds them: a day-by-day reconstruction from ~56,000 scraped transaction records, anchored on year-end roster snapshots, cross-checked against the league's official published standings, and **graded** -- every era carries its own measured error rate, printed in the almanac rather than hidden.

On top of that sits a record book that had to be built from scratch, showing individual performances and statistical outputs at a grain that is not visible on either platform's website.

---

## The two-league story

The pipeline was built for one league on one platform. Adding a second one that shares almost none of the first one's assumptions is the test that made this into a real model, rather than a pile of league-specific SQL.

|                               | League A                       | League B                                                       |
| ----------------------------- | ------------------------------- | --------------------------------------------------------------- |
| Format                        | Head-to-head, weekly matchups  | Season-long points, no matchups                                |
| History available             | Recent seasons, fully recorded | Back to 2001, mostly *not* recorded                            |
| Per-day scoring from platform | Yes                            | **None -- season totals only**                                 |
| Roster history                | Served by the API              | Reconstructed from transactions                                |
| Position eligibility          | Served by the API              | Derived from real MLB game logs, in congress w league settings |

What made this tractable rather than a fork:

- **One warehouse, one set of models, a `league_key` grain.** Per-league schemas were considered and rejected. Every fact and mart carries the league key; the output layer filters. Adding a league does not add a model.
- **Platform vocabulary stops at the staging layer.** Stat ids, slot ids and format labels stay native in staging and are translated to a canonical catalog through mapping seeds. Everything downstream speaks one language. The rules are written down in [docs/platform-adapter-contract.md](docs/platform-adapter-contract.md).
- **Format is a separate axis from platform.** "Points league" and "which website" are independent dimensions, so a feed can be required, optional, or conditional on format rather than on vendor.
- **The universal layer is real baseball.** Stats come from the public MLB Stats API, not from a fantasy vendor, so the underlying numbers are never in doubt. Only the fantasy state around them (who owned whom, who was in the lineup on a given day) has to be reconstructed.

The payoff is measurable: the second league's almanac renders through the *same* tab builders as the first, in the same shape, from a shared convergence layer.

![ESPN Team Tab, current-season and all-time optimal lineups](docs/img/espn-team-tab-comparison.png)

![CBS Team Tab, same layout, same renderer](docs/img/cbs-team-tab-comparison.png)

*Identical layout, rendered from two different platforms, with points calculated according to each league's own scoring settings and each team's actual history -- the single best evidence for the whole adapter thesis. Same tab builder, same columns, fed a different `league_key`.*

---

## Architecture

Four layers, one warehouse, two platforms:

```
ESPN + CBS extract  ->  Snowflake RAW (append-only, platform-native shape)
                    ->  dbt staging (canonicalized to one vocabulary)
                    ->  dbt intermediate (identity resolution, walk-backs)
                    ->  dbt marts (core contracts + reporting)
                    ->  weekly recap · records report · Google Sheets almanac
```

Built on dbt + Snowflake + Python: extract scripts land raw JSON, dbt owns everything from staging through marts, and three Python consumers read the marts to produce the recap, the records report, and the almanac. A full lineage/DAG image is coming once the current model-renaming refactor settles; drawing one today would be stale within weeks. The [hosted dbt catalog](https://kyledawson24.github.io/fantasy-league-front-page/) has the real, current lineage and column-level docs if you want that today; it's regenerated manually and regularly, so treat it as approximate rather than live (as we approach final state, I will update this doc accordingly).

---

## What it produces

**A weekly BBCode recap**: best/worst matchup totals with top contributors, wasted performances, records broken. Written to a timestamped file, ready to paste into ESPN's front page in a format that renders on its stone-age level text editor.

![Week 16 Recap, posted live to the league's ESPN front page](docs/img/espn-recap-posted.png)

*Posted straight to the league's actual ESPN front page; this is Week 16, live, not a mockup. Even the jokey callouts are scripted according to a [purpose built callout-script](output/league_notes.py).*

Alternate Recap, as text:

```
[u][b]Week 6 Recap[/b][/u]

[b]Best Overall[/b]: 309.4 pts by No Power, No Panic
Bobby Witt Jr.: 32.7, Cam Schlittler: 30.9, Pete Alonso: 24.8, ...
[b]Best Hitting[/b]: 184.3 pts by Intentional Walk to the Bar
Andy Pages: 37.4, Pete Crow-Armstrong: 26.4, Matt Olson: 21.2
[b]Best Pitching[/b]: 156.6 pts by Rob Manfred Death Squad
Logan Gilbert: 29.6, Nathan Eovaldi: 25.6, Rico Garcia: 20.7

[b]Top Scorer[/b]: Cristopher Sanchez (SMEL), 52.6 pts -- 15.0 IP, 17 K, 2 W, 2 QS
[b]Top Hitter[/b]: Andy Pages (WALK), 37.4 pts -- .417/.440/.917 -- 24 AB, 4 HR, 8 RBI

[u][b]Top 5 Wasted Performances[/b][/u]
1. Heliot Ramos (SF, LF) -- Atomic Alpacas Assuming Position --
   27.9 wasted pts (3.4 unowned, 20.3 benched, negative 4.2 active)
2. Brandon Marsh (Phi, LF/CF) -- Clase Action Lawsuit -- 26.2 pts
...

[u][b]New Records[/b][/u]
[b]New Worst Player Platform Hitting Points[/b]: Cal Raleigh (NPNP), -6.2 pts -- 8 AB
(Prior: -6.0 pts by Mark Vientos (NPNP) in Semi-Finals of 2025)
[b]Tied Record for Fewest RBIs[/b]: Leno's Manic Mandible at 14 RBI, the 3rd team to do so.
```

*(A real Week 6 recap, lightly trimmed for readability.)*

**An all-time records report**, and **a multi-tab league almanac in Google Sheets**: Home, Records, Advanced Standings, Draft Recap, a matchup history, a trade board, and one page per team. How to read it is documented in the [user guide](docs/user-guide/).

A few of its tabs:

![Firefly Lake Vandals Team Tab](docs/img/cbs-team-tab-flv.png)

*Team Tab: arguably the core deliverable of the project; a team's current-season top producers, its all-time optimal lineup, and every player's actual stat line for their specific time on this team, not their career or league-wide numbers. The single view of "who are my team's guys" as though your fantasy league was a real world competition.*

**One thing this makes possible that no standard fantasy site can answer:** Carlos Martinez spent more time on FLV's roster than almost anyone in franchise history, and if he'd never been benched he would have been the franchise's third-biggest pitching contributor of all time. But he scored over 40% of his points from the bench, and ends up missing the "Starting Lineup" entirely. That said, he was used well: his 3.20 ERA while active for FLV beats his career mark of 3.74 by more than half a run.

That comparison -- not his career line, the line for what he actually did *for this team, in the games this team started him* -- only exists because the pipeline creates visibility into active-vs-benched performance for every player, every day, across 25 years.

![All-League Team](docs/img/espn-all-league-team.png)

*All-League Team: the season's best player at every lineup slot, with the fantasy team that rostered them and what the best bench or free-agent alternative would have scored. Automatically assumes the shape and restrictions of the league's roster settings.*

**Answers questions the native site can't:** "What players have actually had the biggest impact on *this league* this season, as opposed to overall production in the MLB?"

![Lineup Slot Records](docs/img/espn-slot-records.png)

*Lineup Slot Records: the current season's best single week at each slot beside the all-time holder. New records automatically highlighted for the period after their occurrence.*

**Answers questions the native site can't:** "What's the best matchup a shortstop has ever had under our new scoring system?"

![Trade Record](docs/img/espn-trade-record.png)

*Trade Record: every completed trade, grouped, with what each side's pieces actually scored after the deal.*

**Answers questions the native site can't:** "How has the multi-player trade I made 4 months ago worked out for me?" or "What's the biggest blockbuster in league history where the most total points changed hands?"

![Points by Lineup Slot](docs/img/cbs-points-by-slot.png)

*Points by Lineup Slot: production by the slot it was deployed in, paced per standard season so shortened and in-flight years compare honestly.*

**Answers questions the native site can't:** "Which owners historically build their teams around certain positions?" or "Which contending teams this season have an obvious hole in their roster, and would they make good trade partners?"

![Best Individual Seasons by Lineup Slot](docs/img/cbs-best-seasons-by-slot.png)

*Best Individual Seasons by Lineup Slot: the top single season at each slot, back to 2001, re-priced under one rulebook so eras sit on the same scale.*

![All-Time Draft Board](docs/img/draft-recap.png)

*All-Time Draft Board: every pick, re-cut to the league's current team shape, colored by value against that round's historical median; the keeper row ranks by production.*

**Answers questions the native site can't:** "What is the draft pick I was just offered in a trade actually worth?" or "How many points does my first rounder need to score before I can call it a successful decision?"

---

## What's next

v1.x is polish on the current architecture: a player-entity layer (`dim_player` / `fct_player_career`) and more analytics surfaces on data the pipeline already has. v2.0 is structural: Yahoo and Sleeper adapters to prove the platform-agnostic design against a third and fourth platform, and a DuckDB target (see Quick start below) so the project runs without a cloud warehouse. Full detail (including what's been explicitly decided against) is in [ROADMAP.md](ROADMAP.md).

---

## Quick start

**Just here to look?** The screenshots above and the [hosted dbt catalog](https://kyledawson24.github.io/fantasy-league-front-page/) cover the design; [docs/user-guide/](docs/user-guide/) covers how to read the almanac itself.

**Want to run it?** Today that means bringing your own Snowflake account -- the free tier is enough, and [SETUP.md](SETUP.md) will get you most of the way there (~30-45 minutes, mostly Snowflake provisioning); a step-by-step setup wizard is planned for August 2026. A clone-and-run demo mode with no warehouse account at all (DuckDB, packaged sample data, one command) is planned for v2.0 and isn't available yet. A portability spike ([docs/duckdb-portability-audit.md](docs/duckdb-portability-audit.md)) sized the transform-layer port at roughly a focused week of engineering, so this is a scoped near-term plan, not a someday-maybe.

---

## What this demonstrates

The current shape of the transform layer: **72 dbt models** (32 views, 37 tables, 3 incremental), **18 seeds**, **542 data tests**, and **4 declared exposures**, building green end to end.

- **Modeling that survived a second implementation.** Wide convergence facts at consumer grain; a symmetric active/inactive split ("active is fantasy reality, inactive is MLB reality") that is what makes wasted-production analysis possible at all; a seed-driven UNPIVOT mart where adding a tracked stat is a CSV row rather than a five-file SQL change.
- **Reproducibility.** Floating-point sums are not associative, and SQL engines do not promise summation order, so rebuilding with no code change could move a rendered cell by one, and oh boy it often did. Sums now run in exact decimal with pinned tie-breaks, and a byte-diff harness pins a known week so any drift fails loudly.
- **Reconstruction with published error bars.** The walk-back does not claim to know what it cannot know. Where records are simply unavailable we make that clear and render a zero. The resulting under-count is stated per era instead of being smoothed away (while still allowing manual override tables to populate data manually where a "league historian" might know something that the platform's API no longer stores).
- **Cross-language data contracts.** One seed CSV drives both the dbt mart's Jinja loop and the Python display, polarity and record-surfacing logic.
- **Portability assessed, not assumed.** A spike ported the staging layer to DuckDB on real data to size a warehouse-independence effort honestly, including the traps; a 32-bit `FLOAT` that silently narrows values, and an engine default that would have emptied the record book without erroring. Written up in [docs/duckdb-portability-audit.md](docs/duckdb-portability-audit.md).
- **Real-data debugging discipline.** A doubleheader bug in an upstream wrapper found from team totals running ~3.6 points low; a scoring category whose season feed disagrees with its own per-game data; a record book rebuilt after discovering its source neglected significant portions of the player pool. Each documented with the evidence.

---

## Project documentation

- **[docs/user-guide/](docs/user-guide/)** -- how to read the almanac,
  written for league members.
- **[SETUP.md](SETUP.md)** -- bring-your-own-credentials walkthrough.
- **[docs/known-data-issues.md](docs/known-data-issues.md)** -- the list of gaps, caveats, and open questions.
- **[docs/platform-adapter-contract.md](docs/platform-adapter-contract.md)** -- the shape a new platform has to land data in.
- **[CHANGELOG.md](CHANGELOG.md)** · **[ROADMAP.md](ROADMAP.md)** -- version history, and what is Now / Next / Later / Decided Against.
- **Phase X.Y Documentation.md** in the repo root -- the decision log, each with an "options considered → chosen → rationale" section. These were all pre-release/exploratory; their only real purpose is archival.
- **[Hosted dbt catalog](https://kyledawson24.github.io/fantasy-league-front-page/)** -- model lineage and column-level docs, regenerated manually (may lag a release or two behind local `main`).

---

## Status

- **v1.5.1** -- current, 2026-07-25. A correctness pass on the CBS record
  book: fixed non-deterministic rebuilds, a silent transaction-capture gap
  (~408 rows dropped across 26 seasons of history), records that were
  rounded twice, and player identity that gave up whenever a name had two
  candidates. Patch, not minor -- everything in it corrects an existing
  surface rather than adding one.
- **v1.5.0** - 2026-07-21. The multi-league release: a league registry and a `league_key` re-grain of every layer, and the CBS points league (2001-2026) ships end to end through the same tab builders as ESPN. Advanced Standings, Trades, Baseball Reference links, and a reworked Draft Recap land on the ESPN side in the same release.
- **v1.2.0** - 2026-05-30. Home became a navigation-hub dashboard, and a net-new Draft Recap tab (draft board plus draft-value analysis) landed. (1.3 and 1.4 were internal working labels during an unreleased stretch, skipped deliberately to keep the docs unambiguous.)
- **v1.0.0 - v1.1.2** -- the original single-league ESPN foundation: the weekly BBCode recap, the all time records report, and the first Google Sheets almanac. Full per-release history in [CHANGELOG.md](CHANGELOG.md).
- **License**: MIT (see [LICENSE](LICENSE)).
- **Built with**: dbt 1.11 · Snowflake · Python 3.13 · `espn-api` wrapper · `gspread`.

## Contact
Email: kpdawson.github@gmail.com
LinkedIn: https://www.linkedin.com/in/kyledawson24/
Ko-fi: https://ko-fi.com/kpdawson24

## A note on the name

"Fantasy League Almanac" is the working personality name; the repository is still `fantasy-league-front-page` for historical reasons. Don't read too much into either, likely to change.
