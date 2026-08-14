# Setup

The detailed reference for running the pipeline against your own ESPN
Fantasy Baseball league: prerequisites, credentials, league registry, dbt
profile, the first run, and the advanced Snowflake and bring-your-own-Google
routes.

**This is not the supported release path.**
[QUICKSTART.md](QUICKSTART.md) is: download the release ZIP, make a venv,
fill in `.env` and `config/leagues.yml`, and run
`python tools/create_public_almanac.py`. That command owns extraction,
the local parquet/DuckDB build, dbt, and the Google workbook. Come here
when you want the detail behind one of those steps, when you are running
the maintainer's Snowflake path, or when you are bringing your own Google
client.

If you just want to read about what this project does, see
[README.md](README.md).

Expect ~30-45 minutes for first-time setup **if you take the optional
Snowflake path**, mostly waiting for free-tier provisioning. The local
DuckDB path that QUICKSTART uses provisions nothing and skips section 4
and section 5 entirely.

---

## 1. Prerequisites

- **Python 3.13.x** -- not 3.14, which the pinned stack currently crashes
  under (mashumaro, via dbt). Earlier 3.x versions haven't been tested.
- **An ESPN Fantasy Baseball league**. Private leagues are supported and
  are what the release path expects (requires cookies -- see step 3).
  Public leagues should need less, but that has not been proven through
  the `create_public_almanac.py` entrypoint.
- **Git**, only for the developer/source path. The release ZIP does not
  need it.
- **Windows**, only for the public Google workbook flow -- v1.9 stores
  that grant in Windows Credential Locker. The local pipeline (extract,
  DuckDB, dbt, preview files, BBCode) has no such requirement.
- *(Optional, advanced)* **A Snowflake account**, if you want the
  maintainer's warehouse path instead of local DuckDB. It is **not a
  prerequisite**: nothing in the release journey touches it, and it is
  reached only by deliberately passing `--advanced-snowflake`. Free-tier
  (Standard, 30-day trial → $0 storage for small datasets after) is
  plenty. Sign up at https://signup.snowflake.com. Pick any region.
- *(Optional, advanced)* **A Google Cloud project and OAuth client**.
  **Not required for the public workbook path** -- the release build
  ships its own identity. You need your own client only for section 10b:
  writing to a workbook you already created (the records report, or
  `--prod`), or consenting as a client of your own from a source clone.

---

## 2. Clone the repo and create a Python environment

```bash
git clone https://github.com/KyleDawson24/fantasy-league-almanac.git
cd fantasy-league-almanac

# Python 3.13 venv
python -m venv .venv

# Activate (PowerShell)
.venv\Scripts\Activate.ps1
# Or (bash)
source .venv/bin/activate

pip install -r requirements.txt
```

`requirements.txt` is a real pinned freeze; `pip install` should
complete in about a minute with no resolution drama -- measured at 64
seconds into an empty pip cache, most of it spent on two large binary
wheels, DuckDB and pyarrow. If you hit any "can't find package"
errors, double-check you're on Python 3.13.

**One install covers both engines.** The freeze carries `dbt-snowflake`
and `dbt-duckdb` side by side, so the same venv runs the Snowflake
target this guide sets up next *and* the local DuckDB target that
`tools/duckdb_run.sh` and `tools/demo.sh` build against. There is no
second environment to create: those scripts call whatever `dbt` is on
your PATH, which is this venv once it is activated.

---

## 3. ESPN credentials

The pipeline reads from ESPN's private fantasy API. For private
leagues you need two cookies from a logged-in browser session, plus
your league ID.

### Find your league ID

Log in to ESPN Fantasy and navigate to your league's home page. The URL
will look like:

```
https://fantasy.espn.com/baseball/league?leagueId=123456&seasonId=2026
```

Your league ID is the number after `leagueId=`.

### Find your cookies

Open your browser's dev tools while logged in to ESPN Fantasy:

- **Chrome / Edge**: F12 → Application tab → Cookies → `https://fantasy.espn.com`
- **Firefox**: F12 → Storage tab → Cookies → `https://fantasy.espn.com`

Find two cookies:

- `espn_s2` -- long opaque string, ~300 chars
- `SWID` -- UUID wrapped in curly braces, like `{AB12CD34-...}`

Copy both values verbatim (including the `{}` braces on SWID).

> **Note:** ESPN cookies expire periodically. If extraction starts
> failing with 401s, refresh both cookies from a fresh login.

---

## 4. Snowflake setup

### Create the database and warehouse

After your Snowflake trial is provisioned, log into the Snowsight UI
and run these bootstrap commands to create the resources the pipeline
expects:

```sql
-- One-time bootstrap. Run as ACCOUNTADMIN.
-- These create the warehouse, database, and schemas; pick any names
-- you like, but keep them consistent with the .env values you set in
-- step 6 and the dbt profile you write in step 5.
CREATE WAREHOUSE COMPUTE_WH WITH WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;
CREATE DATABASE ESPN_FANTASY;
USE DATABASE ESPN_FANTASY;
CREATE SCHEMA RAW;
CREATE SCHEMA ANALYTICS;
```

The names above (`COMPUTE_WH`, `ESPN_FANTASY`, `RAW`, `ANALYTICS`)
match the defaults in this guide. Customize freely; the pipeline
expects:

- `RAW` schema for the append-only JSON landed by the Python extractor
  (env: `SNOWFLAKE_SCHEMA`)
- `ANALYTICS` schema for the dbt models (env:
  `SNOWFLAKE_ANALYTICS_SCHEMA`, defaults to `ANALYTICS` if unset). Output
  scripts read from this schema; if you pick a different dbt target
  schema, set the env var to match.

### Capture your connection details

You'll need these for both `.env` and the dbt profile:

- **Account identifier**: shown in the Snowsight URL as `<account>.snowflakecomputing.com`. Use the full identifier including region (e.g., `abc12345.us-east-1`).
- **Username**: your Snowflake login.
- **Role**: `ACCOUNTADMIN` for setup; you can rotate to a more restricted role later.
- **Database**: `ESPN_FANTASY` (or whatever you created above).
- **Warehouse**: `COMPUTE_WH`.

### Authentication: key-pair (recommended) vs password

Snowflake supports password authentication, but **the moment MFA is
enforced on your account, password-based scripts stop working** -- the
Python connector can't satisfy an interactive MFA prompt and fails
with `Multi-factor authentication is required for this account`.
MFA enforcement is increasingly default on new accounts.

**Key-pair authentication** sidesteps MFA entirely (it's the
Snowflake-recommended programmatic-access path) and is what the rest
of this guide steers toward. One-time setup, no expiring tokens, works
forever. If your account doesn't yet have MFA enforced, password auth
still works as a fallback -- the pipeline detects which one you've
configured.

#### Generate an RSA key pair

Run this from anywhere; it writes the keys to your home directory:

```bash
python -c "
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import os
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
home = os.path.expanduser('~')
priv = os.path.join(home, '.snowflake_rsa_key.p8')
pub  = os.path.join(home, '.snowflake_rsa_key.pub')
with open(priv, 'wb') as f:
    f.write(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
with open(pub, 'wb') as f:
    f.write(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
print('Private key:', priv)
print('Public key body to paste into ALTER USER:')
body = open(pub).read().replace('-----BEGIN PUBLIC KEY-----', '').replace('-----END PUBLIC KEY-----', '').replace('\\n', '').strip()
print(body)
"
```

The script prints the public-key body (with PEM headers stripped, one
line) -- that's what goes into the Snowflake `ALTER USER` command below.

The private key file (`~/.snowflake_rsa_key.p8`) needs to stay on your
machine. Don't commit it; don't share it. The path goes into `.env` in
step 6.

> **Encrypted private keys (optional).** If you want the private key
> encrypted with a passphrase, change the `encryption_algorithm` line
> above to `serialization.BestAvailableEncryption(b'your-passphrase')`
> and set `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` in `.env` to match. For a
> single-user personal project the unencrypted key is reasonable since
> `.env` is gitignored alongside it; an encrypted key matters more in
> shared environments.

#### Register the public key on your Snowflake user

In Snowsight, open a worksheet and run (substituting your username and
the public-key body from the previous step):

```sql
ALTER USER your_username SET RSA_PUBLIC_KEY='MIIBIjANBgkq...QIDAQAB';

-- Verify:
DESC USER your_username;
-- Look for RSA_PUBLIC_KEY_FP -- a fingerprint string indicates the key registered.
```

You can also skip the `python -c` script above and use Snowflake's
own openssl-based instructions in their docs if you prefer; either
PKCS8 RSA key works.

---

## 5. dbt profile

dbt looks for connection profiles at `~/.dbt/profiles.yml` (Linux/Mac)
or `C:\Users\<you>\.dbt\profiles.yml` (Windows).

Create the file. **Pick one auth path:**

**Key-pair auth (recommended):**

```yaml
dbt_league:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: <account.region>
      user: <username>
      private_key_path: /full/path/to/.snowflake_rsa_key.p8
      # private_key_passphrase: only-set-for-encrypted-keys
      role: ACCOUNTADMIN
      database: ESPN_FANTASY
      schema: ANALYTICS
      warehouse: COMPUTE_WH
      threads: 4
      client_session_keep_alive: false
```

**Password auth (only if MFA is NOT enforced):**

```yaml
dbt_league:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: <account.region>
      user: <username>
      password: <password>
      role: ACCOUNTADMIN
      database: ESPN_FANTASY
      schema: ANALYTICS
      warehouse: COMPUTE_WH
      threads: 4
      client_session_keep_alive: false
```

Verify the connection:

```bash
cd dbt_league
dbt debug
```

You should see `All checks passed!`. If `dbt debug` fails with an MFA
error, you're on password auth against an MFA-enforced account --
switch to the key-pair stanza above.

---

## 6. Configure `.env`

Copy the template and fill in real values:

```bash
cp .env.example .env
```

Edit `.env` with the values from steps 3 + 4. Match the auth path you
picked for the dbt profile in step 5 (key-pair recommended; password
fallback only for accounts without MFA enforced):

**Key-pair auth (recommended):**

```bash
# ESPN
ESPN_S2=<paste full espn_s2 value here>
SWID={...}
LEAGUE_ID=<your league ID>

# Snowflake
SNOWFLAKE_ACCOUNT=<account.region>
SNOWFLAKE_USER=<username>
SNOWFLAKE_DATABASE=ESPN_FANTASY
SNOWFLAKE_SCHEMA=RAW
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_PRIVATE_KEY_PATH=/full/path/to/.snowflake_rsa_key.p8
# SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=only_set_for_encrypted_keys
# Only set if your dbt target schema isn't named ANALYTICS:
# SNOWFLAKE_ANALYTICS_SCHEMA=ANALYTICS
```

**Password auth (only if MFA is not enforced on your account):**

```bash
# ESPN ... (same as above)

# Snowflake
SNOWFLAKE_ACCOUNT=<account.region>
SNOWFLAKE_USER=<username>
SNOWFLAKE_PASSWORD=<password>
SNOWFLAKE_DATABASE=ESPN_FANTASY
SNOWFLAKE_SCHEMA=RAW
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
```

The output scripts' `db.init()` detects which one you've configured:
when `SNOWFLAKE_PRIVATE_KEY_PATH` is set, it uses key-pair auth;
otherwise it falls back to password.

Skip the Google Sheets vars for now (see section 10 if you want that
sink later).

**CBS league (optional -- only if you archive a second-platform league):**

```bash
# CBS (all browser-extracted; the login is reCAPTCHA-walled, so there
# is no scripted credential flow -- and these leagues are treated as
# read-only museums by the capture scripts)
CBS_LEAGUE=<league slug, e.g. bsb>
CBS_TOKEN=<fantasy API access_token -- extraction steps below>
CBS_WEB_COOKIES=<the whole `cookie:` request-header value from devtools>
```

Where the token actually hides (re-verified 2026-07-18 -- the page has
SEVERAL token-shaped variables; the old "Ctrl+F `var token`" tip no
longer matches):

1. Open any logged-in league page (e.g. `/standings/overall`) and View
   Page Source. The right value is the `access_token` inside the
   **global player-search function** (~128 chars) -- the search widget
   calls the same `api.cbssports.com/fantasy` API the capture scripts
   do, which is what makes it the right one. The similarly-shaped CBSi
   token is CBS Interactive's site-wide identity -- not it.
2. Zero-guess alternative: DevTools → Network → filter `fantasy` →
   fire the player-search box → read `access_token=` off any
   `api.cbssports.com/fantasy/...` request URL.

`CBS_TOKEN` drives the API captures (`extract/cbs_capture.py`,
`extract/cbs_backfill.py`); `CBS_WEB_COOKIES` drives the site-page
archive (`extract/cbs_ui_capture.py`). Both expire eventually -- the
token's one observed lifetime is 2–11 days (minted 2026-07-07, alive
07-09, dead by 07-18), so assume roughly a week and re-extract when a
run reports auth failure. The scripts refuse to land anything
unauthenticated, so expiry can't corrupt the archive.

> `.env` is gitignored. Never commit credentials. The .env.example
> template stays tracked for new-user onboarding.

---

## 7. Tell it about your league

### The league registry (`config/leagues.yml`)

This is the one configuration file the release path genuinely requires,
and it is the file `create_public_almanac.py` reads to decide *which*
league to build and *how far back*. Open it and edit the `espn-main`
entry, or add your own beside it:

```yaml
default_league: espn-main

leagues:
  espn-main:
    platform: espn
    display_name: "My league"
    league_id_env: LEAGUE_ID
    credential_env: [ESPN_S2, SWID, LEAGUE_ID]
    first_season: 2019
    final_season: null
```

- **`display_name`** -- a label for logs and docs, never keyed on. Pick
  something non-identifying; it does not have to be the league's real
  name.
- **`first_season`** -- the earliest ESPN season that belongs to this
  league. The public command builds every season from here forward, so a
  wrong value is the most common cause of a short or failing history.
- **`final_season`** -- `null` while the league is ongoing, or the final
  year once it has finished.
- **`default_league`** -- must name the entry you want built. With more
  than one entry, pass `--league LEAGUE_KEY` rather than relying on the
  default.
- **`credential_env`** -- every `.env` variable the adapter needs at
  extract time. The loader reports which are missing, and
  `create_public_almanac.py` checks them before it runs anything.

**No secrets and no private league id go in this file** -- it is tracked
in git. The entry names the `.env` variable holding the id
(`league_id_env`) instead. A genuinely public league could use a literal
`league_id:`; when both are present the literal wins.

The header comment in `config/leagues.yml` is the full field reference,
including why league format is derived from the platform rather than
configured here.

### The league_config seeds

Seeds are CSV files dbt loads into the warehouse. This project keeps them
in two directories, and the difference is the whole point:

```
dbt_league/seeds/          reference vocabulary -- ships filled in
dbt_league/league_config/  YOUR league -- ships blank
```

`seeds/` is stat maps, MLB team abbreviations, and which stats count as
records. It is identical for every league on a platform, so it arrives
complete and you should never need to open it.

`league_config/` is everything that differs between leagues: your
calendar, your franchises, your owners, and the overrides that rename and
merge things. On a fresh clone every file there is a **blank template** --
a header row and nothing else. That is deliberate. You are not meant to
inherit anybody else's league.

**[`dbt_league/league_config/README.md`](dbt_league/league_config/README.md)
documents every file with a worked example.** Read that one, not this
section, when you are actually filling them in.

### What you have to fill in, and what you can ignore

Two kinds of file live there, and they behave differently when left blank.

**Required** -- something reads these directly, so blank means the
surfaces that depend on them come out empty:

- `cbs_franchises.csv`, `cbs_team_owners.csv`, `team_owner_by_year.csv`,
  `draft_assembly_plan.csv` -- CBS leagues only. ESPN serves this
  information through its own API, so an ESPN-only league leaves them
  blank.

`matchup_schedule.csv` **used to be on that list and is not any more.**
An ESPN league no longer fills it in at all:

- **Which scoring periods each matchup period contains** comes from
  ESPN's own `mMatchupScore` response, captured automatically on every
  box-score run. That is what the extract now selects weeks from, so it
  is not optional data that happens to be nice to have -- it is the
  thing the run reads.
- **The dates** come from arithmetic, not from you. ESPN's scoring
  periods are calendar days, so scoring period N is the season's first
  scoring date plus N-1 days, and a matchup period's start and end are
  its first and last scoring period. The anchor is MLB's own published
  regular-season start date, fetched from the free public MLB Stats API
  (`statsapi.mlb.com/api/v1/seasons`) on the same run -- no key, no
  account. Measured against the maintainer's hand-maintained calendar it
  reproduces all 44 closed periods of 2025 and 2026 exactly, long opening
  weeks and 14-day All-Star weeks included, and a standing dbt test fails
  the build if the two ever disagree rather than letting the calendar
  move quietly.

  If that anchor cannot be established -- if MLB's API were unreachable on
  every run you have made, say -- dates stay empty rather than being
  guessed, and the surfaces that need one say so instead of showing a
  number that means something else. A single failed run costs nothing: the
  extract warns, box scores still land, and the next successful run fills
  the calendar in.

**Optional** -- these only rename, merge, or repoint things. Every one of
them reaches the pipeline through a left join, so blank means "change
nothing" and everything still builds:

- `owner_nicknames.csv`, `owner_alias.csv` -- how owners display, and
  which owner ids are the same person.
- `franchise_lineage.csv` -- which franchise ids are the same franchise
  across a renumbering.
- `player_nicknames.csv`, `player_alias.csv`,
  `player_identity_overrides.csv`,
  `player_identity_context_overrides.csv` -- player naming and identity.
- `matchup_schedule.csv` -- now a **correction and labelling** surface
  rather than a source. Fill in a row only when you want to override a
  derived date for a commissioner-declared oddity, or to carry a human
  label the platform does not serve (a playoff round name, a written
  reason a week was unusual). A blank file is the normal state.
- `matchup_period_overrides.csv` -- the sparse escape hatch for a week
  that is genuinely abnormal but whose *length* looks ordinary, which
  derivation cannot see. Still the right tool for that, and still sparse.

So the honest minimum for an ESPN league is now **no files at all**.
Everything in this directory can stay blank until something displays in
a way you want to change.

### Seeing it work before you fill anything in

You do not have to configure *your* league to render an almanac.
`demo/league_config/` is a complete fake league -- a fixture, tracked in
git, containing no real-league data:

```bash
tools/demo.sh
```

That builds and renders off the fixture in its own warehouse
(`data/duckdb/demo/`) and writes the almanac tabs as TSV. It never reads
`dbt_league/league_config/`, so it cannot pick up anything of yours, and
it needs no Snowflake account and no Google credentials.

**It is not a clean-clone demo, though, and the distinction matters.** It
*transforms* raw league data; it does not land any, and it will not
invent any -- so on a clone that has never run an extract it says so and
stops. That makes it a build-and-render wrapper for someone who already
has data (maintainer scaffolding, in practice), not the "clone it and
look" path. The packaged sample league that would make it that path is
tracked as MLB-11, scoped to v2.1, and is not built yet.

### Switching between them

Both directories are seed roots for the same models. `DBT_LEAGUE_CONFIG`
picks which one is live, relative to `dbt_league/`:

```bash
# your league (the default)
dbt seed

# the demo fixture
DBT_LEAGUE_CONFIG=../demo/league_config dbt seed
```

Models resolve seeds by filename, so nothing else changes -- which is why
the two directories must always hold the same set of files with the same
columns. A test enforces that
(`tests/test_league_config_templates.py`).

---

## 8. First run

You now have everything to run the full pipeline end-to-end.

```bash
# From repo root
python extract/extract.py            # Extract recent matchup periods
                                      # (or: python extract/extract.py 1 2 3
                                      # for specific weeks)

cd dbt_league
dbt deps                              # Install dbt_utils package (first
                                      # run only; idempotent)
dbt seed                              # Load the 20 seed CSVs -- 6 reference
                                      # + 14 from league_config (section 7)
dbt build                             # Build 97 models + run 720 tests

cd ..
python output/generate_summary.py     # Weekly recap BBCode
python output/generate_records_report.py --no-sheets
                                      # All-time records report;
                                      # --no-sheets skips the Sheets sink
                                      # (use any time you don't want
                                      # to write Sheets)
```

Each output script prints BBCode to stdout AND writes a timestamped
file to `output/logs/`. Copy from either into your ESPN league's front
page editor.

You should see something like:

```
[u][b]Week 6 Recap[/b][/u]
[b]Best Overall[/b]: 354.9 pts by ...
```

If you see errors during extract: refresh ESPN cookies, check
`LEAGUE_ID`. If you see errors during dbt build: re-check `dbt debug`.
See section 11 for common gotchas.

### Upgrading an existing install: the club-of-game backfill (REQUIRED)

**Skip this if you are setting up for the first time** -- a fresh extract
lands the field already. This is for a warehouse that was loaded before
the club-of-game flip.

The MLB team credited for a player's production used to come from
`proTeam`, ESPN's stamp on the *person* record: the club that player
belonged to when the period was pulled. For a season pulled in one pass,
that is the same club for every day of the year, so anyone traded
mid-season had their whole season misfiled under the club they finished
at. The chain now reads `clubOfGame` -- the club of the *game* the
production came from -- which does not decay.

**Old RAW has no `clubOfGame` key at all.** It is written by a backfill
pass, not by the transform, so an existing warehouse has nothing for the
models to read. Run it once per already-loaded season:

```bash
python extract/extract.py --backfill-club-of-game --all --year 2025
python extract/extract.py --backfill-club-of-game --all --year 2026
```

**`--all` is load-bearing.** Without it the run backfills only the
periods that ended in the last 21 days, which for a season that is
already over is none of them: it would finish quietly, change nothing,
and leave the build failing with no clue why. `--all` means every
completed period of that season.

To backfill specific weeks instead, pass them as positional arguments
(`... --backfill-club-of-game --year 2025 7 8 9`); there is no
`--matchup-period` flag.

The backfill only ever ADDS a key. It updates rows in place, deletes
nothing, and leaves `loaded_at` alone, so it is safe on settled history
and safe to re-run -- a half-finished pass is resumed by running it again.

You do not have to remember to do this. `dbt build` fails with
`assert_club_of_game_migrated` until you have, and the failure names this
command. That gate exists because the alternative was worse than an
error: before it, an un-migrated install built **every model green**
while its historical affinity chart silently went null/Unattributed. A
green build that quietly produces a blank chart is the failure mode worth
spending a test on.

---

## 9. Verify the test suite

### The three tiers

Every command in this repo falls into one of three tiers. The tier tells
you what it needs and what it touches -- worth knowing before you run
something that rebuilds your warehouse.

| Tier | Needs | Touches | Commands |
|---|---|---|---|
| **1 -- offline** | nothing but the clone | nothing | `pytest tests/`, `dbt deps`, `dbt parse`, `dbt compile` |
| **2 -- live, read-only** | Snowflake creds | reads only | `dbt debug`, `dbt source freshness`, `dbt docs generate`, `dbt ls`, `pytest tests/ -m warehouse`, output scripts with `--no-sheets` |
| **3 -- mutation & regeneration** | creds + intent | **writes** | `dbt seed`, `dbt build`, `--full-refresh` variants, `python extract/*.py`, `REGENERATE_BASELINES=1 pytest`, output scripts *without* `--no-sheets` |

Tier 1 is what CI runs (see `.github/workflows/ci.yml`) and what a
reviewer can run on a fresh clone with no account. Tier 3 is ceremony:
nothing in it should be a reflex.

### Tier 1: the pure suite

```bash
pytest tests/
```

Fresh-clone collection at this cut: **1629** pure tests, with **27**
warehouse-marked tests deselected by default via `pytest.ini`; no
credentials are involved and nothing is written. Those are what pytest
COLLECTS, not a pass tally -- how many pass or skip varies by machine and
OS. These counts drift between releases -- `pytest tests/ -q` is the
truth.

### Tier 2: the warehouse suite

```bash
pytest tests/ -m warehouse
```

This collects **27 tests**. It reads your warehouse and subprocess-runs
the output scripts, but does not write to the warehouse.

**How many of the 27 actually run depends on corpora you do not have.**
See below.

### Which tests need what

Some regression corpora are **private and will never be in this repo**.
They are rendered from the maintainer's real league, owner names
throughout, so they live locally and on a private remote only:

| Corpus (gitignored) | Guards | Without it |
|---|---|---|
| `tests/fixtures/baseline_summary_current.txt`<br>`tests/fixtures/baseline_records_report.txt` | recap + records BBCode byte-diff | `test_golden_output.py` **skips** |
| `tests/fixtures/almanac_v1_1_0/` | ESPN almanac TSV byte-diff | `test_almanac_byte_diff.py` **skips** |
| `tests/fixtures/cbs_almanac/` | CBS almanac TSV byte-diff | `test_cbs_almanac_byte_diff.py` **skips** |

These **skip, they do not fail** -- a fresh clone gets a green run with
skips, not red X's. That is deliberate: a stranger's clone should never
show failures for data they were never given.

### Pinning goldens to *your* league (tier 3)

The byte-diff harness is genuinely useful once it is anchored to your own
data. To generate your own baselines after a successful end-to-end run:

```bash
REGENERATE_BASELINES=1 pytest tests/ -m warehouse
```

This **writes fixture files** -- hence tier 3. After regenerating, future
runs lock to your league's expected output and catch regressions on
rebuilds. Re-anchor deliberately and review the diff; a golden that moves
without a reviewed cause is the bug the harness exists to catch.

---

## 10. Optional: Google Sheets sink

Two different routes live here, and which one you want depends on whether
you are publishing a new workbook or writing into one you already own.

### 10a. A new share-ready almanac workbook -- no Google Cloud project

```bash
python tools/create_public_almanac.py
```

This is post-configuration orchestration: the `.env` fields and selected
`config/leagues.yml` entry must already be complete. The guided fields-file
onboarding remains MLB-31/MLB-207, so this is not yet described as the complete
stranger onboarding journey.

The command reuses the registry's `first_season` / `final_season` policy and
runs one complete extraction for every applicable season—verified matchup
membership and calendar, every closed H2H matchup or every reportable scoring
day in a measured ESPN season-long-points season, settings, standings,
draft/owners, and transactions. A failed or underivable season stops before
DuckDB build or workbook generation. It forces the local Parquet/DuckDB
pipeline before creating the workbook. Snowflake is used only when you deliberately add
`--advanced-snowflake`. The lower-level
`python output/generate_almanac_sheet.py --new-public-workbook` command also
defaults to DuckDB.

**No Google Cloud project, no enabled API, no OAuth client of your own,
and no environment variable.** The released build ships its own Google
identity, as installed applications do; it lives in a Cloud project used
for nothing else, and it is not the maintainer's client.

**Released build, not a clone.** The credential is injected into the
release archive by `tools/build_release_bundle.py` and is deliberately
absent from tracked source. GitHub's partner secret scanning reads public
repository history out of band from anything a repository can configure,
reports supported credentials to Google, and leaves Google deciding
whether the credential stays valid -- and history cannot be rewritten
after the fact. So consumers download
`fantasy-league-almanac-<version>.zip` from the Releases page and run from
inside it. A clone is the developer path: everything else works, and this
one command stops with a message saying the build shipped no identity.
That is the fail-closed state working, and section 10b is how a developer
gets past it.

It requests exactly one permission, `drive.file`, which Google classifies
as non-sensitive: an app holding it sees only the files it created
itself. It cannot enumerate, open, or read the rest of your Drive. On the
first run your browser opens Google's consent screen; later runs use the
grant stored in Windows Credential Locker and normally open no browser.

The run creates a private workbook and renders the almanac into it. Immediately
before changing sharing, it identifies the real league/member information the
workbook may contain and requires you to type `YES`. Automation must supply
`--confirm-link-sharing` as its explicit affirmation. Only after that does the
app set anyone-with-the-link **viewer**, read the permission back, and print
`Your almanac: <link> -- share-ready.` A refusal or failure withholds that line
and prints the workbook URL with a plain explanation. The workbook is never
deleted to clean up after a failure.

Local state stays local. The public Google grant is encrypted at rest by
Windows Credential Locker; v1.9 supports this public flow on Windows only and
fails closed if that backend cannot be used. It never falls back to a
plaintext cache. An older `output/.sheets_public_oauth_token.json` is migrated
only after the secure write succeeds and is verified, then removed. The
workbook ledger (`output/.sheets_public_workbooks.json`), DuckDB/Parquet data
and ESPN cookies remain ordinary local files protected by OS/file permissions
and `.gitignore`. Revoke the grant at
[myaccount.google.com](https://myaccount.google.com/permissions). A revoked or
expired refresh token triggers fresh consent on the next run; nothing here
expires on your behalf.

**Branding review is still pending.** The shipped identity's publishing
status is **In Production**: it is not restricted to a test-user list,
and the roughly week-long grant expiry that applies in Google's testing
mode does not apply to it. The homepage, Privacy Policy and Terms are
live at [kpdawson.com](https://kpdawson.com), and `drive.file` is
non-sensitive -- Google reports that data-access verification is not
required for it. What remains is branding verification, which has been
submitted and not yet reviewed. Until it is approved Google may withhold
the configured branding and may show an unverified-app warning on the
consent screen. That is a statement about branding review, not about
reach: `drive.file` is still the only scope the public path requests, so
it can see the workbook it creates and nothing else in your Drive.

### 10b. Advanced: bring your own OAuth client

You need this route if you are writing to a workbook you already created
(the records report, or `--prod`), or if you want to consent as a client
of your own rather than the shipped one. It is an override, not a
prerequisite for 10a.

Two separate environment variables, deliberately never interchangeable:

- `GOOGLE_OAUTH_CLIENT_PATH` -- the **maintainer** profile. Requests the
  `spreadsheets` scope and opens EXISTING workbooks by id. A `drive.file`
  client can never see a workbook it did not create, so this is the only
  route to a sheet you made by hand.
- `GOOGLE_PUBLIC_OAUTH_CLIENT_PATH` -- overrides the shipped identity for
  the `--new-public-workbook` path. Set it and it wins; leave it unset and
  the shipped identity is used. A path that is set but wrong is an error,
  never a silent fall-back.

The two profiles have different scopes, different client configs and
different token caches, and neither can read or overwrite the other's.

### Create a GCP project + OAuth client

1. Go to https://console.cloud.google.com and create (or pick) a
   project. Any name; the project is just an OAuth container.
2. Enable the Google Sheets API and Google Drive API for the project.
3. Configure the OAuth consent screen -- pick **External** user type;
   leave most fields default. Add yourself as a test user. Don't worry
   about app verification; this is a personal-use OAuth client.
4. Credentials → Create credentials → OAuth client ID → **Desktop app**.
   Download the resulting JSON file; save somewhere safe outside the
   repo (e.g., `~/credentials/oauth-client.json`).

### Create the target Sheet

Create a new Google Sheet in your account. Grab its ID from the URL:

```
https://docs.google.com/spreadsheets/d/<this_part_is_the_id>/edit
```

The script auto-creates the three tabs (`All-Time Records`, `Current
Season Records`, `Leaderboard Dump`) on first write.

### Add the env vars

Append to `.env`:

```bash
GOOGLE_OAUTH_CLIENT_PATH=/absolute/path/to/oauth-client.json
SHEETS_OUTPUT_ID=<your sheet ID>
```

### First-run flow

Run the records report:

```bash
python output/generate_records_report.py
```

On the first invocation, the script opens a browser for OAuth consent.
Approve, and the script caches the resulting token at
`output/.sheets_oauth_token.json` (gitignored) so future runs are
silent.

Subsequent runs write to your Sheet without prompting.

---

## 11. Common gotchas

**`401 Unauthorized` during extract.** ESPN cookies expired. Refresh
both `espn_s2` and `SWID` from a fresh browser session.

**`dbt debug` fails with auth error.** Wrong account identifier format.
Use the full `<account>.<region>` (e.g., `abc12345.us-east-1`), not
just `abc12345`.

**Everything builds green but the weekly surfaces are empty.** This used
to mean a blank `dbt_league/league_config/matchup_schedule.csv`, and it
no longer does -- weeks come from ESPN's own response now, so a blank
seed is the normal state and not a cause. Check instead that the extract
actually captured a matchup schedule: an ordinary box-score run prints
`Matchup schedule for <year>` followed by how many closed matchup periods
it found. Zero closed periods early in an ordinary H2H season is real and
simply means nothing has finished yet. ESPN season-long points is different:
measured league type 5 follows a daily roster path and does not wait for its
one season-spanning reporting container to close. Then confirm `dbt seed &&
dbt build` ran after the extract -- a period present in RAW but missing
downstream is usually a build that has not caught up.

The same shape explains a green build with unnamed CBS franchises or no
CBS owner history: `cbs_franchises.csv`, `cbs_team_owners.csv` and
`team_owner_by_year.csv` are also read directly rather than as overrides.
The seeds that genuinely do nothing when blank are the naming and merging
ones -- those are listed as "optional" in section 7 and in
`dbt_league/league_config/README.md`.

**`dbt seed` succeeds but `dbt run` complains about missing seed
columns.** Re-run with `--full-refresh`:

```bash
dbt seed --full-refresh
```

dbt's incremental seed loader can skip on schema mismatches; full
refresh forces a clean recreate.

**`pytest tests/ -m warehouse` reports a pile of skips.** Expected, not a
problem. The byte-diff corpora are private (section 9, "Which tests need
what") so the tests that need them skip in your clone. To anchor them to
your own league after a successful end-to-end run:

```bash
REGENERATE_BASELINES=1 pytest tests/ -m warehouse
```

**Tempted to enrich the output BBCode?** Don't. ESPN's front-page
renderer supports only `[b]bold[/b]`, `[u]underline[/u]`, and
`[i]italics[/i]` -- hyperlinks, images, embeds, color tags, and most
other standard BBCode get stripped or rendered literally. The output
formatters in `output/formatters.py` are tuned to that constraint; if
you want fancier output, target the Sheets sink instead.

**Snowflake bills you anyway.** The free tier covers most usage but
the warehouse will auto-resume on every dbt run. If you forget about
it for months you can rack up small charges. Set up a billing alert
in Snowsight to be safe.

---

## What you have after setup

Running the pipeline weekly:

```bash
python extract/extract.py        # ~30 seconds for current week
cd dbt_league && dbt build       # ~60 seconds for full build
cd .. && python output/generate_summary.py
python extract/cbs_capture.py --capture   # CBS league (if configured):
                                 # idempotent -- extends the roster
                                 # sweep to today, lands new standings
                                 # periods + fresh transaction/config
                                 # snapshots. Runs LAST on purpose: a
                                 # CBS token expiry must never block
                                 # your own league's update. Skip if
                                 # you have no CBS_TOKEN in .env.
```

The full output is in `output/logs/<timestamp>.txt`. Paste into your
league's front-page editor and ship it.

See [ROADMAP.md](ROADMAP.md) for what's coming next.
