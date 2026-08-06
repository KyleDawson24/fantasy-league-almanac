# Quickstart

Get your own ESPN fantasy baseball league through this pipeline and out
the other side as a browsable almanac. Fill in a handful of fields, run
five commands.

This is the short path. [SETUP.md](SETUP.md) is the long one, and every
step here links into it at the section that explains the step properly.
When the two disagree, SETUP.md is right.

## What you need

- **Python 3.13.x** and **git**.
- **Your ESPN league id.** It is the number in your league's URL.
- **Two ESPN cookies**, `espn_s2` and `SWID`, if your league is private.
  Public leagues do not need them.
- **A Snowflake account.** This is the part people are surprised by, so
  it is stated first rather than discovered on step five. The transform
  layer does build on DuckDB as well as Snowflake, which is easy to read
  as "no warehouse needed" and is not what it means: nothing lands raw
  league data anywhere but Snowflake, so that is where your league has to
  arrive before any of it can be built. A free trial account covers all
  of this. A packaged sample league that skips the requirement entirely
  is tracked on the roadmap and is not here yet.

## 1. Clone and install

```bash
git clone https://github.com/KyleDawson24/fantasy-league-almanac.git
cd fantasy-league-almanac

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

## 2. Fill in your `.env`

```bash
cp .env.example .env
```

Then open `.env` and fill it in. Every field in that file carries a
comment naming the exact SETUP.md section that tells you where its answer
comes from, so you should never have to guess or go hunting.

`.env` is gitignored, and so is every `*.env`. Your cookies and your
Snowflake credentials stay on your machine.

## 3. Point dbt at Snowflake

dbt reads its connection profile from `~/.dbt/profiles.yml`, which is
outside this repo. Copy the block from
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

```bash
python extract/extract.py                 # pull recent weeks from ESPN

cd dbt_league
dbt deps                                  # first run only
dbt seed
dbt build                                 # models + tests
cd ..

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
- **Snowflake is required**, per the note at the top.

## Something hurt?

This path is young. It has been walked by the person who wrote it and not
many others, which is exactly the kind of testing that misses things.

If a step was wrong, ambiguous, or assumed something you did not have,
open an issue and say where you got stuck. That feedback is what 2.0 is
for, and a report that says "step 3 made no sense because X" is more
useful here than a patch.
