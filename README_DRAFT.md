# Fantasy Beat Reporter

> **DRAFT — not live.** This is a proposed rewrite of `README.md` for
> review. It does not replace anything until Kyle says so. Screenshot
> slots are marked `[SCREENSHOT: ...]`; the version numbers in Status are
> marked `[VERIFY]` because I did not re-derive them tonight.

> Two fantasy baseball leagues, twenty-five years of history, one
> pipeline. It reconstructs seasons nobody recorded, re-prices every
> stat under one rulebook so eras can be compared honestly, and ships
> the result as a weekly recap and a browsable league almanac.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![dbt](https://img.shields.io/badge/dbt-1.11-orange)](https://www.getdbt.com/)
[![Snowflake](https://img.shields.io/badge/warehouse-Snowflake-29B5E8)](https://www.snowflake.com/)
[![Python](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)

Real product, real users, real weekly cadence — and a deliberate
analytics-engineering portfolio piece. Jump to [What this
demonstrates](#what-this-demonstrates) if you are here to evaluate the
engineering.

[SCREENSHOT: the almanac Home tab in Google Sheets — navigation band on
the left, All-League Team boards on the right. This is the money shot;
it should be the first thing a visitor sees.]

---

## The 60-second version

Every Sunday, one command turns a week of fantasy baseball into a
formatted recap: best and worst matchups with their top contributors,
the wasted-performance callouts, and any records broken or tied. Paste,
post, done.

That was the original product. The interesting part is what it grew
into.

The same transform layer now serves **two leagues on two different
platforms with two different formats** — a head-to-head league where
teams play each other weekly, and a season-long points league whose
history runs back to **2001**. Neither league's platform will tell you
what its own rosters looked like fifteen years ago. So the pipeline
rebuilds them: a day-by-day reconstruction from ~56,000 scraped
transaction records, anchored on year-end roster snapshots, cross-checked
against the league's official published standings, and **graded** —
every era carries its own measured error rate, printed in the almanac
rather than hidden.

On top of that sits a record book that had to be rebuilt from scratch
once, because the platform's own historical archive turned out to cover
only free agents — silently omitting every star who was actually
rostered.

---

## The two-league story

This is the part worth reading, because it is where the architecture
earned its keep.

The pipeline was built for one league on one platform. Adding a second
one that shares almost none of the first one's assumptions is the test
that separates a real model from a pile of league-specific SQL.

| | League A | League B |
|---|---|---|
| Format | Head-to-head, weekly matchups | Season-long points, no matchups |
| History available | Recent seasons, fully recorded | Back to 2001, mostly *not* recorded |
| Per-day scoring from platform | Yes | **None — season totals only** |
| Roster history | Served by the API | Reconstructed from transactions |
| Position eligibility | Served by the API | Derived from real MLB game logs |

What made this tractable rather than a fork:

- **One warehouse, one set of models, a `league_key` grain.** Per-league
  schemas were considered and rejected. Every fact and mart carries the
  league key; the output layer filters. Adding a league does not add a
  model.
- **Platform vocabulary stops at the staging layer.** Stat ids, slot ids
  and format labels stay native in staging and are translated to a
  canonical catalog through mapping seeds. Everything downstream speaks
  one language. The rules are written down in
  [docs/platform-adapter-contract.md](docs/platform-adapter-contract.md).
- **Format is a separate axis from platform.** "Points league" and
  "which website" are independent dimensions, so a feed can be required,
  optional, or conditional on format rather than on vendor.
- **The universal layer is real baseball.** Stats come from the public
  MLB Stats API, not from a fantasy vendor, so the underlying numbers are
  never in doubt. Only the fantasy state around them — who owned whom,
  who was in the lineup — has to be reconstructed.

The payoff is measurable: the second league's almanac renders through
the *same* tab builders as the first, in the same shape, from a shared
convergence layer.

[SCREENSHOT: the two almanacs' team pages side by side, showing the
identical layout rendered from two different platforms. This is the
single best evidence for the whole adapter thesis.]

---

## What it produces

**A weekly BBCode recap** — best/worst matchup totals with top
contributors, wasted performances, records broken. Written to a
timestamped file, ready to paste into the league's front page.

[SCREENSHOT: a posted recap on the league's actual front page — the
product in its native habitat, not a terminal.]

**An all-time records report**, and **a multi-tab league almanac in
Google Sheets**: Home, Records, Advanced Standings, Draft Recap, a
matchup history, a trade board, and one page per team. How to read it is
documented in the [user guide](docs/user-guide/).

[SCREENSHOT: the Records tab, current season and all-time side by side.]

[SCREENSHOT: the Advanced Standings rank-by-week chart.]

---

## Quick start

> **[PLACEHOLDER — needs a rewrite pass.]** The current instructions
> assume the head-to-head league only and predate the second platform.
> This section should end up as: a 5-minute "just look at the outputs"
> path for a reader who will never run it, and a separate "run it against
> your own league" path pointing at SETUP.md. Both need re-testing
> end-to-end before publishing.

If you want to read about the design: keep going, then see the
[hosted dbt catalog](https://kyledawson24.github.io/fantasy-league-front-page/).

If you want to run it against your own league: [SETUP.md](SETUP.md).

---

## What this demonstrates

The current shape of the transform layer: **72 dbt models** (46 views, 23
tables, 3 incremental), **18 seeds**, **542 data tests**, and **4 declared
exposures**, building green end to end.

- **Modeling that survived a second implementation.** Wide convergence
  facts at consumer grain; a symmetric active/inactive split ("active is
  fantasy reality, inactive is MLB reality") that is what makes
  wasted-production analysis possible at all; a seed-driven UNPIVOT mart
  where adding a tracked stat is a CSV row rather than a five-file SQL
  change.
- **Reproducibility taken seriously.** Floating-point sums are not
  associative, and SQL engines do not promise summation order — so
  rebuilding with no code change could move a rendered cell by one, and
  did. Sums now run in exact decimal with pinned tie-breaks, and a
  byte-diff harness pins a known week so any drift fails loudly.
- **Reconstruction with published error bars.** The walk-back does not
  claim to know what it cannot know. Where activity is unknowable it
  contributes zero rather than a guess, and the resulting under-count is
  stated per era instead of being smoothed away.
- **Cross-language data contracts.** One seed CSV drives both the dbt
  mart's Jinja loop and the Python display, polarity and
  record-surfacing logic.
- **Portability assessed, not assumed.** A spike ported the staging layer
  to DuckDB on real data to size a warehouse-independence effort
  honestly, including the traps — a 32-bit `FLOAT` that silently narrows
  values, and an engine default that would have emptied the record book
  without erroring. Written up in
  [docs/duckdb-portability-audit.md](docs/duckdb-portability-audit.md).
- **Real-data debugging discipline.** A doubleheader bug in an upstream
  wrapper found from team totals running ~3.6 points low; a scoring
  category whose season feed disagrees with its own per-game data; a
  record book rebuilt after discovering its source was free-agents-only.
  Each documented with the evidence.

---

## Project documentation

- **[docs/user-guide/](docs/user-guide/)** — how to read the almanac,
  written for league members.
- **[SETUP.md](SETUP.md)** — bring-your-own-credentials walkthrough.
- **[docs/known-data-issues.md](docs/known-data-issues.md)** — the honest
  list of gaps, caveats, and one open question.
- **[docs/platform-adapter-contract.md](docs/platform-adapter-contract.md)**
  — the shape a new platform has to land data in.
- **[CHANGELOG.md](CHANGELOG.md)** · **[ROADMAP.md](ROADMAP.md)** —
  version history, and what is Now / Next / Later / Decided Against.
- **Phase X.Y Documentation.md** in the repo root — the decision log,
  each with an "options considered → chosen → rationale" section.
- **[Hosted dbt catalog](https://kyledawson24.github.io/fantasy-league-front-page/)**
  — model lineage and column-level docs.

---

## Status

`[VERIFY]` — the released version and its date need confirming against
CHANGELOG.md before this goes live; the existing README's Status list
stops at v1.2.0 and is stale.

- **License**: MIT (see [LICENSE](LICENSE)).
- **Built with**: dbt 1.11 · Snowflake · Python 3.13 · `gspread`.

## A note on the name

"Fantasy Beat Reporter" is the working personality name; the repository
is still `fantasy-league-front-page` for historical reasons. Don't read
too much into either.
