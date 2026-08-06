# Release Notes -- v1.7.0

The first public release. 129 commits since v1.6.0.

The engine port, the new surfaces, the truth pass and the hardening batch all add or correct without changing the shape of the thing. 

The one exception is the club-attribution flip, which moves rendered values and needs a
migration step on existing installs; it is gated by a test that fails the build until the step is done, so it announces itself rather than surprising anyone. See **Upgrading** at the bottom.

v2.0 is reserved for the release where somebody else can run this. That bar is at the end, under **What 2.0 is for**.

---

## The club attribution flip

The headline correction. Especially relevant to my fellow fans of the affinity table, but its effect reaches almost all ESPN leagues.

**ESPN-specific**
Production used to be credited to the club on ESPN's *player record*, which is the club that player belongs to right now. For a season loaded after the fact, "right now" is the same club for every day of the year, so anyone traded mid-season had their whole season filed under wherever they finished. 

Measured against 2025 it was not small: **22.25% of the chart's weight was filed under the wrong club, and a further 11.7% could not be placed at all** and rendered in an Unattributed band.

Both of those issues are past tense now. 

Each per-scoring-period split already carried the club the player was actually with when he produced, and the chart reads that instead. The Unattributed band measured 0.0 across 2025, 2026 and all-time on the flip, and has been retired from the rendered surfaces rather than kept as an empty row.

The measurement and the fix both stay in [Known Data Issues](docs/known-data-issues.md). 
A closed defect is still part of the record, and this one was found in our own work rather than reported by anyone.

## The engine port
The transform layer builds on **DuckDB as well as Snowflake**.
Engine-specific SQL sits behind adapter-dispatch macros, the output layer can read either, and the full 74-model chain builds in a single run at a 6 GB memory cap with engine threads pinned.

Read that precisely, because it is easy to hear as more than it is: nothing lands raw league data anywhere but Snowflake yet. The port means you can build and render against a local warehouse you already have, not that the warehouse requirement is gone. Removing it is what the packaged sample league is for, and that is still ahead.

By 2.0, the extract itself will write locally to DuckDB, rather than porting Snowflake data in.

Along the way the port found things worth keeping: a 32-bit `FLOAT` that silently narrowed values, an engine default that would have emptied the record book without erroring, and a decimal division only one engine was rounding. The spike that sized the work is in [docs/duckdb-portability-audit.md](docs/duckdb-portability-audit.md).

## New surfaces
**CBS Season History**, on the platform lens: the points league's season by season record, rebuilt in the points-league format and carrying Matchup History's highlighting rules

We deviated for this specific view from our usual preference for "calculated" or re-priced over platform points for a fairly simple reason; CBS relies more heavily on reconstruction, and the roster pictures are therefore less reliably complete for pre-2020 data. Platform Scores, however, remain accessible at least as far back as 2001, meaning we can reliably report those figures and track where teams actually finished in their seasons.

**Two Halls on the head-to-head book.** 
A career Hall of Shame split by production type, and the futility block that both books now share, with the records tabs put in lockstep across platforms so the same column means the same thing on either side. On the records tab; shows all-time contribution by player/team combo, plus the players whose MLB careers have been put to the worst use by your league.

## Runnable by someone who is not me
Seeds used to be one directory doing three unrelated jobs. They are now three, and which directory a file sits in tells you what it is: `dbt_league/seeds/` is reference vocabulary that ships filled in, `dbt_league/league_config/` is your league's configuration and ships as **blank templates**, and `demo/league_config/` is an explicitly named demo fixture holding a complete fake league.

So a clone gets blank templates plus a working fixture, and the honest minimum for an ESPN league turns out to be one file.

The caveat stays attached, in the same words the README uses: a credential-free clone-and-run demo does **not** exist yet. `tools/demo.sh` builds and renders, but it does not land raw data and will not invent any, so on a clone that has never run an extract it says so and stops.

**Want it on your league?** [QUICKSTART.md](QUICKSTART.md) is the new short path: the fields to fill, the commands to run, one screen, every step linked into SETUP.md. It is a young path, walked by the person who wrote it and not many others. Open an issue where it hurts. That is what this release is for.

It is also an interim path, and says so at the top. It still asks you to make a Snowflake account, because that is still true today. Deleting that requirement is the whole job of the next release.

## The quality arc

This release went through a cold review, a surface scrub, and a fail-closed hardening batch, in that order.

The scrub was about the public face telling the truth at HEAD: claims narrowed to what the evidence supports, a catalog landing page that was 47 models out of date rewritten, and the error ladder stated as the ladder it is rather than one flat number. 

The reconstruction is **26 seasons of data (2001-2026), 25 completed**; the estimated stretch runs roughly unbiased 2005-2010, undershoots about 8-13% from 2011, and about 20% for 2020, the short COVID season with the thinnest log. 2001-2003 is a coverage gap rather than an accuracy one and is labelled directional wherever it appears.

The hardening batch fixed six things, four of them the same shape: a question that could not be answered being read as the reassuring answer. A demo isolation guard that checked a variable it never exported. 
A settled-history guard that read every database error as "no table, so
nothing to protect". 
A failed API fetch encoded as a valid empty
response, which on one path would have erased stored club labels
wholesale. 
And the PII guard, which on a clean clone printed a warning,
swept nothing at all, and exited green -- so "the guard passed" and
"nothing was checked" were the same exit code. It is strict by default
now, and it matches a whole identity family rather than the one spelling
that happened to be stored.

The suite is **515 pure tests** and **544 dbt data tests**.

---

## What 2.0 is for

One sentence, and everything else is downstream of it:

> **A stranger with an ESPN or CBS league enters some credentials, runs some things, and gets an almanac their league can open.**

Read "an almanac their league can open" literally. Files on disk are what this release gives you; the goal is a workbook in your own Drive with link sharing already set, because a league almanac the league cannot open is a demo rather than a product.

**ESPN end to end is the hard requirement and the gate.** 
CBS is a first-class goal rather than a stretch, and it is being priced now instead of promised now: threading the league key, parameterizing the capture, a per-league error-bar wrapper, and documented config knobs. The charter session rules whether that lands in 2.0 proper or immediately after.

Underneath is a failure-cost argument, not a feature list. Bugs are certain in something this young. Somebody who filled in a few fields and hit one shrugs and stays interested; somebody who created a cloud account, provisioned a warehouse and generated a key pair before hitting the same bug leaves annoyed, and fairly so. Tolerance for failure scales inversely with what you demanded upfront, so the upfront demand has to be close to zero.

The charter is **MLB-210**, and it is where the goal lives until the roadmap is re-cut from it. Two keystones:

- **MLB-208 -- extract writes RAW locally.** The engine port is genuinely
  done, but ingestion's front door was never in its scope: `extract.py`
  still lands data only in Snowflake, so the no-warehouse path does not
  exist yet. This release found that seam while writing the quickstart,
  which is why the quickstart names the Snowflake account in its first
  list rather than burying it on step five.
- **MLB-209 -- the journey ends in a shareable workbook.** The render
  pipeline already writes Sheets; what is missing is the stranger's
  credential story, not a code hole.

Then **MLB-207** (the fields file becomes executable, validating your
credentials before the long run so a bad cookie fails at minute one
rather than minute forty), **MLB-204** (filled league config moves to a
gitignored root, so `git add .` cannot publish your league), and
**MLB-11** (a packaged sample league, for trying it with no league of
your own).

Version numbers are promises here. 2.0 ships when that journey is real
and not before, which is exactly why this cut is 1.7.0.

---

## Upgrading

**Existing installs must run the club-of-game backfill.** This is not
optional and `dbt build` will fail until it is done.

The chain reads `clubOfGame`, and old RAW has no such key: it is written
by a backfill pass, not by the transform. Before this release an
un-migrated install built every model green while its affinity chart
silently went null. Now `assert_club_of_game_migrated` fails the build
and names the command.

Run it once per already-loaded season:

```bash
python extract/extract.py --backfill-club-of-game --all --year 2025
python extract/extract.py --backfill-club-of-game --all --year 2026
```

`--all` matters: without it the run covers only periods that ended in
the last 21 days, which for a finished season is none of them, and it
would exit quietly having changed nothing.

The backfill only ever adds a key. It updates in place, deletes nothing,
leaves `loaded_at` alone, and is safe to re-run, so an interrupted pass
is resumed by running it again.

Full detail in [SETUP.md](SETUP.md#8-first-run) and
[CHANGELOG.md](CHANGELOG.md).
