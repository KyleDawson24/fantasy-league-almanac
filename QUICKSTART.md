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
ESPN-only install (noted in step 5).

The ESPN half is walked end to end, and as of this release it needs one
fewer thing from you: **there is no schedule to fill in**. What is still
outstanding on the ESPN side is packaging rather than data -- there is
no single bootstrap command and no guided fields file yet, so the five
commands in step 5 are still five commands.

Writing to a live Google Sheet no longer needs a Google Cloud project or
an OAuth client of your own; the tool ships its own identity. That path
is step 6, and it carries one real caveat you should read before
counting on it.

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

## 4. Nothing. There is no step 4 any more

**This used to be "tell it when your weeks start and end".** You had to
open `dbt_league/league_config/matchup_schedule.csv` and type every
week's boundaries before anything worked, and leaving it blank left
every weekly surface empty.

You do not any more, and there is nothing to do here. The extract asks
ESPN which scoring periods belong to each matchup period and reads the
answer straight out of the response, so your weeks come from your
league rather than from a file you maintain.

The dates come automatically too. ESPN does not spell out calendar dates
in that response, but its scoring periods are **days** -- so once the
season's first scoring date is known, scoring period N is that date plus
N-1 days, and each matchup period's start and end are just the first and
last scoring period in it. The opener comes from MLB's own published
regular-season start date, fetched from the free public MLB Stats API
that this project already uses. Nobody types a calendar.

Every file in `dbt_league/league_config/` can now stay blank. They only
rename, merge, relabel or correct things, and they all reach the
pipeline through a left join, so blank means "change nothing". See
[SETUP.md section 7](SETUP.md#7-tell-it-about-your-league) for the file
by file breakdown, including when you would still want to fill one in.

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
downstream of it.

`dbt seed` and `dbt run` themselves are clean on exactly the installation
described above -- every `league_config` file left blank, nothing but ESPN
data in RAW. All 20 seeds load and all 85 models build; the CBS-side models
come out empty, which is the true thing to say about a league with no CBS
configuration. That is checked by a test, not by memory, because it was
briefly untrue: an empty CSV has no data to infer a column type from, so a
few CBS columns used to arrive as the wrong type and take three models down
with them. The types are declared explicitly now.

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

## 6. Optional: put it in a Google Sheet you can share

Everything above writes files. This step writes your almanac into a
**brand-new Google Sheet in your own Drive** and hands you a link.

After the fields and registry setup in steps 1–2, the supported
**post-configuration** release path is one command. It is not yet the complete
fields-file onboarding journey tracked by MLB-31/MLB-207.

The command reads the selected registry entry's existing `first_season` /
`final_season` bounds. It requests each applicable season exactly once with
full closed-period box scores, season settings, transactions, matchup
membership, and calendar; any underivable season stops before a workbook can
be generated. It then loads local Parquet, builds DuckDB, and creates the
workbook:

```bash
python tools/create_public_almanac.py
```

Snowflake is never selected by accident. It is available only through the
deliberate `--advanced-snowflake` option. If you already completed the local
build above, the lower-level workbook-only command is
`python output/generate_almanac_sheet.py --new-public-workbook`; that command
also forces DuckDB unless `--advanced-snowflake` is present.

**You do not need a Google Cloud project, an enabled API, or an OAuth
client of your own.** The released build ships its own Google identity,
the way any installed app does. There is nothing to create and no path to
configure.

**It has to be the released build, though, and not a clone.** The Google
credential is packaged into the release archive; it is deliberately not
in this repository's source, because a credential in public git history
is reported to Google by GitHub's own scanning and cannot be removed
afterwards. So:

- **Using the tool?** Download `fantasy-league-almanac-<version>.zip`
  from the [Releases page](https://github.com/KyleDawson24/fantasy-league-almanac/releases),
  unzip it, and run the commands above from inside it. That copy carries
  the identity.
- **Working on the code?** A `git clone` is the developer path, and it is
  the right thing -- everything else in this guide works from it. But
  this one step will stop with a message saying the build shipped no
  identity. That is correct, not a bug. Point
  `GOOGLE_PUBLIC_OAUTH_CLIENT_PATH` at an OAuth client of your own
  ([SETUP.md section 10](SETUP.md#10-optional-google-sheets-sink)) if you
  need the live path from a checkout.

What happens, in order:

1. It prints what it is about to ask Google for.
2. Your browser opens Google's normal consent screen. You pick an account
   and approve.
3. It creates a new spreadsheet and renders the full almanac into it while
   the workbook is still private.
4. Immediately before sharing, it names the league/member information in the
   workbook and asks you to type `YES`. Only then does it set
   anyone-with-the-link **viewer**, read that permission back to confirm, and
   print `Your almanac: <link> -- share-ready.` Automation must deliberately
   pass `--confirm-link-sharing` to make the same affirmation without a prompt.

If any of those three steps does not happen, you do not get that line --
you get the workbook's URL and a plain sentence about what went wrong.
The workbook is yours either way; it is never deleted to tidy up.

The permission it asks for is `drive.file`, which Google classifies as
non-sensitive: **it can only see files it creates itself.** It cannot
list, open, or read anything else in your Drive. Only the new workbook is
shared; nothing already in your Drive changes.

Later runs reuse the public Google grant stored by Windows Credential Locker
and do not normally open a browser. The v1.9 public release supports this
flow on Windows only and refuses to fall back to plaintext if Credential
Locker is unavailable. A legacy `output/.sheets_public_oauth_token.json` is
migrated only after a secure write is verified, then its plaintext copy is
removed. The workbook ledger, DuckDB/Parquet data and ESPN cookies remain
ordinary local files protected by OS/file permissions and `.gitignore`.
Nothing is uploaded anywhere except the almanac you asked to be written (or
your own Snowflake account if you deliberately choose the advanced path).
Revoke the Google grant at
[myaccount.google.com](https://myaccount.google.com/permissions) --
whenever you choose, since nothing here expires it for you.

### The caveat: this identity is not open to the public yet

The shipped Google identity is currently in Google's **testing** mode.
That means:

- only Google accounts added as test users can complete the consent
  screen -- everyone else is turned away by Google, not by this tool;
- grants issued in testing mode **expire after about a week**, so you
  would be asked to consent again;
- the consent experience may differ from the eventual production branding.

Moving it to production needs a published homepage, privacy policy, terms
and branding review. That work is tracked and is a release gate. Until it
lands, treat this step as working-but-not-yet-open: the code path is real
and tested, and if you are not a test user Google will stop you.

If you would rather use your own OAuth client -- maintainers, and anyone
working from a clone or testing against a different Google project --
that route is an advanced override, described in
[SETUP.md section 10](SETUP.md#10-optional-google-sheets-sink). It is not
a prerequisite for anything above.

## What you get

`out/almanac_preview/` holds one tab-separated file per almanac tab: the
records, the standings, the matchup history, a draft recap, and a page
per team. Open them in anything.

`python output/generate_summary.py` prints a weekly recap as BBCode, ready
to paste into a league message board.

And, from step 6, a Google Sheet in your own Drive with a link you can
paste into your league chat.

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

  Three things that sound alike and are not. An ordinary run captures the
  **current** season's weeks by itself. `--matchup-schedule-only --year
  2025` captures **2025's weeks only**, in one request. `--all-seasons
  --matchup-schedule-only` captures the weeks for **every season your
  league registry bounds** -- and only the weeks: it downloads no
  historical box scores, which are the slow part and still go a season
  at a time.

- **Season-long points and rotisserie leagues are not proven.** The
  extract accepts a league that reports no head-to-head matchup periods,
  or one season-spanning period, without inventing weeks that do not
  exist -- but its box-score route is head-to-head shaped and has never
  been run against a real league of that kind. Rather than guess, it
  stops and says so, and reports what your league's settings actually
  said. If that is your league, an issue with what it printed is genuinely
  useful.

## Something hurt?

This path is young. It has been walked by the person who wrote it and not
many others, which is exactly the kind of testing that misses things.

If a step was wrong, ambiguous, or assumed something you did not have,
open an issue and say where you got stuck. That feedback is what 2.0 is
for, and a report that says "step 3 made no sense because X" is more
useful here than a patch.
