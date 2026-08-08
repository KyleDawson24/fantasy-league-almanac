# Quickstart

Get your own ESPN fantasy baseball league through this pipeline and out
the other side as a browsable almanac. Fill in a handful of fields, run
five commands.

This is the short path. [SETUP.md](SETUP.md) is the long one, and every
step here links into it at the section that explains the step properly.
When the two disagree, SETUP.md is right.

**There are two paths below, and the local one is new.** An ESPN league
can now go all the way from league id to rendered almanac with **no
warehouse account anywhere** -- raw data lands as parquet files in the
repo and is built in DuckDB. The Snowflake path still exists, is still
the default, and is what the maintainer runs weekly.

This is not yet the whole of v2.0. That goal is a stranger with an ESPN
**or CBS** league getting an almanac with no warehouse accounts, and the
CBS half is not here: its ingestion still runs on browser-extracted
credentials, and one CBS data test does not yet no-op on an
ESPN-only install (noted in step 5). The ESPN half is done and walked.

## What you need

- **Python 3.13.x** and **git**. The version matters: on 3.14 dbt dies at
  startup with an unrelated-looking `mashumaro` traceback about a field
  named `schema`. If you see that, you are on the wrong Python.
- **Your ESPN league id.** It is the number in your league's URL.
- **Two ESPN cookies**, `espn_s2` and `SWID`, if your league is private.
  Public leagues do not need them.
- **No warehouse account, if you take the local path.** Raw data lands as
  parquet files under `data/parquet/raw/` and is built in DuckDB, which
  is a file, not a service. Nothing to sign up for.

  This used to say a Snowflake account was required, and for the local
  path that is no longer true. If you would rather run on Snowflake --
  the maintainer does -- a free trial covers it, and the extra steps are
  marked *Snowflake path* below.

## 1. Clone and install

```bash
git clone https://github.com/KyleDawson24/fantasy-league-almanac.git
cd fantasy-league-almanac

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

That takes about a minute, and it installs both dbt adapters --
Snowflake and DuckDB -- so there is no second environment to create
later. It does not remove the Snowflake requirement above.

## 2. Fill in your `.env`

```bash
cp .env.example .env
```

Then open `.env` and fill it in. Every field in that file carries a
comment naming the exact SETUP.md section that tells you where its answer
comes from, so you should never have to guess or go hunting.

`.env` is gitignored, and so is every `*.env`. Your cookies and your
Snowflake credentials stay on your machine.

On the local path `.env` needs only three fields -- `LEAGUE_ID`,
`ESPN_S2` and `SWID`. Every `SNOWFLAKE_*` field can stay empty.

## 3. Point dbt at a warehouse -- *Snowflake path only*

**Skip this step entirely on the local path.** DuckDB's profile is
already in the repo at `dbt_league/profiles/`, and step 5 passes it
explicitly. There is nothing to create and no `~/.dbt/profiles.yml`
involved.

For Snowflake: dbt reads its connection profile from
`~/.dbt/profiles.yml`, which is outside this repo. Copy the block from
[SETUP.md section 5, "dbt profile"](SETUP.md#5-dbt-profile) and fill in
the same account details you just put in `.env`.

If you have not created the Snowflake database and warehouse yet, that is
[SETUP.md section 4, "Snowflake setup"](SETUP.md#4-snowflake-setup).

## 4. Tell it when your weeks start and end

Open `dbt_league/league_config/matchup_schedule.csv` and add your season's
week boundaries. This is the one file an ESPN league genuinely has to
fill in: nothing else in the pipeline knows when your weeks began and
ended, so leaving it blank leaves every weekly surface empty.

Everything else in that directory can stay blank. Those files only
rename, merge or repoint things, and they all reach the pipeline through
a left join, so blank means "change nothing". See
[SETUP.md section 7](SETUP.md#7-tell-it-about-your-league) for the file
by file breakdown.

## 5. Run it

### The local path -- no warehouse

Every command runs from the repo root.

```bash
python extract/extract.py --raw-target local --include-settings --include-transactions
```

```bash
python tools/load_parquet_to_duckdb.py
```

```bash
dbt deps --project-dir dbt_league --profiles-dir dbt_league/profiles
```

```bash
dbt seed --project-dir dbt_league --profiles-dir dbt_league/profiles
```

```bash
dbt run --project-dir dbt_league --profiles-dir dbt_league/profiles
```

```bash
python output/generate_almanac_sheet.py --duckdb --no-sheets --preview-dir out/almanac_preview
```

The first command writes parquet under `data/parquet/raw/`; the second
loads it into `data/duckdb/ESPN_FANTASY.duckdb`. `--include-settings` and
`--include-transactions` are needed on the FIRST run only -- afterwards
plain `python extract/extract.py --raw-target local` picks up recent
weeks. `dbt deps` is also first-run only.

`dbt run` rather than `dbt build` here, and the reason is a known rough
edge rather than a preference: `dbt build` also runs the CBS data tests,
and one of them (`assert_cbs_scoring_feed_matches_seed`) compares the CBS
scoring feed against a seed that documents it. On an ESPN-only install
the feed is legitimately empty, so the test fails and skips everything
downstream of it. The ESPN models themselves build clean -- 74 of 74.

### The Snowflake path

```bash
python extract/extract.py
```

```bash
cd dbt_league && dbt deps && dbt seed && dbt build && cd ..
```

```bash
python output/generate_almanac_sheet.py --no-sheets --preview-dir out/almanac_preview
```

## What you get

`out/almanac_preview/` holds one tab-separated file per almanac tab: the
records, the standings, the matchup history, a draft recap, and a page
per team. Open them in anything.

`python output/generate_summary.py` prints a weekly recap as BBCode, ready
to paste into a league message board.

Writing all of this into a live Google Sheet instead of files is
optional and needs a Google OAuth client. That is
[SETUP.md section 10](SETUP.md#10-optional-google-sheets-sink); nothing
above requires it.

## What this path does not cover yet

Said plainly, because finding out later is worse:

- **ESPN leagues only.** The CBS side of this project is real and ships
  in the almanac, but its ingestion runs on browser-extracted
  credentials behind a reCAPTCHA login, so there is no scripted setup for
  a second CBS league. Tracked publicly on the roadmap.
- **Recent seasons come through at full fidelity.** Older ones depend on
  what your platform still serves. This repo reconstructs a
  quarter-century for its own two leagues, and the honesty notes about
  where that reconstruction is estimated rather than recorded are in the
  [README](README.md#the-two-league-story) and
  [Known Data Issues](docs/known-data-issues.md).
- **The local path is ESPN-only.** A CBS league still needs the
  browser-extracted credential route above, and `dbt build` still trips
  one CBS data test on an ESPN-only install (step 5).
- **One season, to start.** ESPN serves settings, transactions and the
  draft for the CURRENT season only, so a first local run gets this year.
  Prior seasons' *box scores* can still be pulled -- add a year and the
  weeks you want, as in `python extract/extract.py --raw-target local
  --year 2025 5`, or `--year 2025 --all` for the season. The other feeds
  have no such route.

## Something hurt?

This path is young. It has been walked by the person who wrote it and not
many others, which is exactly the kind of testing that misses things.

If a step was wrong, ambiguous, or assumed something you did not have,
open an issue and say where you got stuck. That feedback is what 2.0 is
for, and a report that says "step 3 made no sense because X" is more
useful here than a patch.
