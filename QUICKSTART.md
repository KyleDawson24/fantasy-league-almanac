# Quickstart

Turn your ESPN fantasy baseball league into a browsable almanac in a Google Sheet your league can open. Install one program, edit two files, run one command.

**This guide assumes you have never used a terminal.** Every required command is written out to be copied exactly.

**Windows is required if you want the Google Sheet output in v1.9.** This release protects your Google sign-in with Windows Credential Locker. The downloading, local database, and preview-file parts can run on macOS or Linux, but publishing the finished workbook from those systems is not supported yet. A comparable secure sign-in path for other operating systems is planned, but it does not have a promised release date.

[SETUP.md](SETUP.md) is the detailed reference behind every step here.
When the two disagree, SETUP.md is right.

## Get the right download

**If you want the Google Sheet, use the release ZIP named `fantasy-league-almanac-<version>.zip`.** Download it from the [Releases page](https://github.com/KyleDawson24/fantasy-league-almanac/releases) and unzip it. The release handles the Google sign-in setup for you, so you do not need Git, a Google Cloud project, or an OAuth client of your own.

`git clone` is the developer path. A source-code clone deliberately does not contain the Google sign-in configuration packaged into a release, so its Google step will refuse to run unless a developer supplies a client separately. If you are simply building your league's almanac, use the release ZIP.

**This is guided setup, but it is not a wizard yet.** You will open and edit two supplied text files. After that, one command downloads, builds, and publishes the almanac. A form-driven setup is planned for a future release.

The development branch now has a bounded candidate of that work: the root-level `START_ALMANAC.cmd` front door prepares a private Python environment, installs or resumes the pinned dependencies, opens a bundled illustrated ESPN-cookie guide, runs [guided setup](docs/setup-preflight.md), validates ESPN access and the requested history, atomically fills the existing local credential and registry files, provides an explicit validated path for rotating expired ESPN cookies, and offers to start the existing public almanac runner. It uses root-relative argument-vector handoffs so extracted folders containing spaces or living under OneDrive do not corrupt commands. It does not replace the manual v1.9.1 release steps below until that candidate is committed, packaged, and successfully rehearsed from the actual ZIP on a clean Windows machine.

## Even quicker start

The full walkthrough below explains every click. The technical outline is:

1. Unzip the release on a Windows PC and install Python 3.13.
2. Open PowerShell inside the extracted folder.
3. Create `.venv` and install `requirements.txt` with the two commands in step 4.
4. Copy `.env.example` to `.env`; add your ESPN league id and two browser cookies.
5. In `config\leagues.yml`, change the display label and the first/final season values.
6. Run `tools\create_public_almanac.py`; choose a Google account and approve link sharing when asked.

If any phrase in that summary is unfamiliar, keep reading from step 1. Nothing in the summary is an extra step.

## What you need

- **A Windows PC**, assuming you want the Google Sheet output.
- **Python 3.13** -- step 1 below gets it installed.
- **An ESPN fantasy baseball league that you can sign into.** Private leagues are the proven path. The v1.9 command also requires sign-in cookies for a public league; step 5 shows how to collect them.
- **A Google account**, for the finished Sheet.
- **No Snowflake account.** Your league's data lands as files on your own
  disk. Snowflake is an advanced option, reached only by deliberately
  passing `--advanced-snowflake`.
- **No Google Cloud project or personal OAuth client**, as long as you are using the release ZIP. The tool handles that setup.

## 1. Install Python 3.13

Download it from the official [Python releases for Windows](https://www.python.org/downloads/windows/) page. Choose the newest available **Python 3.13.x** and its **Windows installer (64-bit)**. Do not choose 3.14; this project does not support it yet.

In the installer, if you see a checkbox labelled **"Add python.exe to
PATH"**, tick it before clicking Install. It makes step 3 work.

Step 3 opens the terminal and checks that this worked. If Windows itself blocks the installer because of system policy, use **Run as administrator** only if you own or administer that PC. The almanac commands later in this guide should run from an ordinary PowerShell window and do not need administrator access.

## 2. Unzip the download

`fantasy-league-almanac-1.9.0.zip` is a **compressed** folder. This is the almanac ZIP, not the Python installer you just downloaded. Windows will happily show you what is inside it without ever unpacking it, and the commands below will not work in that view.

Right-click the ZIP file → **Extract All…** → **Extract**. That produces a
normal folder named something like `fantasy-league-almanac-1.9.0`. Work
inside **that** folder from here on.

## 3. Open PowerShell in that folder

PowerShell is Windows' command terminal. The trick below opens it already
pointed at the right folder, so you never have to navigate:

1. Open the extracted `fantasy-league-almanac-1.9.0` folder in File Explorer, so its contents -- including `QUICKSTART.md` and `requirements.txt` -- are displayed.
2. Left-click once on the **address bar** at the top (the strip showing the folder path). The path becomes editable and highlighted.
3. With the folder's contents still displayed below it, type `powershell` into that address bar and press **Enter**.

A blue or black window opens. The text at the prompt should end with your
extracted folder's path. If it does not, close it and repeat -- being in
the wrong folder is the most common reason a command "does not work".

**Run the commands one at a time.** Copy one command, paste it (right-click
pastes in PowerShell), press Enter, and **wait for the prompt to come
back** before doing the next one. Some steps take a while and look like
nothing is happening.

**Now check Python.** In the PowerShell window, run:

```powershell
python --version
```

The answer must begin with `Python 3.13`. If it says `Python 3.14`, you installed an unsupported version. If Windows says something like *"python is not recognized"*, close PowerShell, open a fresh one using the same address-bar steps, and try again. If it still fails, re-run the Python installer and make sure the "Add python.exe to PATH" box is ticked.

## 4. Create the environment and install the parts

Your prompt should look roughly like `PS C:\Users\YourName\...\fantasy-league-almanac-1.9.0>`. The exact folders before the almanac name will differ, and that is fine.

Run these two commands **one at a time**. The first makes a private Python environment inside the almanac folder:

```powershell
python -m venv .venv
```

It normally prints nothing. When the `PS ...>` prompt appears again, it is finished. Then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This downloads and installs the Python tools used to read ESPN data, build the local database, and write the workbook. It prints many lines and may take a few minutes the first time. When the prompt appears again, it is finished.

> **Optional context:** the `.\.venv\Scripts\python.exe` prefix in later commands points directly at the private environment you just created. That avoids asking you to "activate" it with a script that Windows commonly blocks. You do not need to remember this explanation; copy the commands as written.

## 5. Fill in your `.env` file

This file holds your ESPN sign-in. Two commands: the first creates it from
the supplied template, the second opens it in Notepad.

```powershell
Copy-Item -LiteralPath '.env.example' -Destination '.env'
```

```powershell
notepad .env
```

`.env` looks like a strange filename -- it has no name before the dot --
but it is an ordinary text file and Notepad is perfectly sufficient.

You are replacing **three** values. Find these lines near the top and put your own values after the `=` sign, with no spaces around it:

```
LEAGUE_ID=
ESPN_S2=
SWID=
```

Leave every other value alone. The Snowflake section is an advanced alternative and stays blank for this quickstart. The Google section also stays blank because the release ZIP already contains the sign-in configuration that creates your new workbook.

If any of the three values is hard to find, [SETUP.md section 3](SETUP.md#3-espn-credentials) is the detailed reference for this exact step.

### Where those three values come from

Sign in to ESPN Fantasy in **Chrome or Edge** and open your league.

**`LEAGUE_ID`** is in the address bar. In a URL like
`https://fantasy.espn.com/baseball/league?leagueId=123456&seasonId=2026`,
your league id is the `123456` part -- the number right after
`leagueId=`.

**`ESPN_S2` and `SWID`** are browser cookies. With the league page open:

1. Press **F12**. A developer panel opens beside or below the page.
2. Click the **Application** tab. (You may need the `»` arrow to find it.)
3. In the left sidebar, under **Storage**, expand **Cookies** and click
   the ESPN Fantasy entry (`https://fantasy.espn.com`).
4. A table of cookies appears. Find the rows named **`espn_s2`** and
   **`SWID`**.
5. Click each one and copy its **Value** exactly -- the whole thing.
   `espn_s2` is very long, around 300 characters. `SWID` is short and
   wrapped in curly braces like `{AB12CD34-...}`.
6. Press F12 again to close the panel when you are done.

**Keep the `{` and `}` braces on SWID.** Paste each value exactly as copied -- no quotes added, no spaces, nothing trimmed. The long `ESPN_S2` value may wrap onto several visible lines in Notepad; that is fine. It must still be one actual line in the file, so do not press Enter inside it.

Browser versions move these labels around. If yours says *Storage* instead
of *Application*, or nests things differently, the two cookie names are
still `espn_s2` and `SWID` and the Value column is still what you want.
[SETUP.md section 3](SETUP.md#3-espn-credentials) has more detail.

**Save the file (Ctrl+S) and close Notepad** before continuing.

> **Keep these private.** Treat `ESPN_S2`, `SWID`, and the finished `.env`
> file like a password. They stay on this computer; never email, screenshot,
> or post them. Nothing in this guide asks you to send them to anyone,
> including the author.

## 6. Describe your league in `config\leagues.yml`

This tells the tool which league to build and how far back its history
goes.

```powershell
notepad config\leagues.yml
```

Find the uncommented `espn-main:` entry immediately below the real `leagues:` line. The file's explanatory comments mention `espn-main` earlier; those are not the entry to edit. Do not add a second block beneath `default_league: espn-main`.

**Change only these three values in the existing entry:**

- `display_name`
- `first_season`
- `final_season`

Leave `platform`, `league_id_env`, `credential_env`, and the entire `sinks` section exactly as supplied. The relevant part should look like this, with your own display name and first season:

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
    sinks:
      bbcode: true
      sheets_almanac_env: SHEETS_OUTPUT_ID
      sheets_dev_env: SHEETS_DEV_ID
```

What each line means:

- **`first_season`** -- the first year *this same ESPN league* existed.
  **Replace the `2019` in the example with your league's real first
  year.** The tool builds every season from that year forward, so a wrong
  value is the most common cause of a run that is too short or that fails
  on a year your league did not exist.
- **`final_season: null`** -- `null` is how YAML says "nothing here". Leave
  it as `null` if your league is still running. If it has permanently
  ended, put the last year instead, like `final_season: 2024`.
- **`display_name`** -- a label for logs only. `"My league"` is fine; it
  never has to be your league's real name.
- **`default_league` and `espn-main:`** are already paired correctly. Leave both names alone for this quickstart.

**Do not put your league id or cookies anywhere in this file.** Only the three values named above should change. `league_id_env: LEAGUE_ID` means "look it up in `.env`", where it belongs.

One YAML rule matters: indentation is part of the file's meaning. Keep the leading spaces exactly as supplied and use spaces rather than tabs.

**Save (Ctrl+S) and close Notepad.**

Everything in `dbt_league/league_config/` can stay blank -- those files
only rename and relabel things, so blank means "change nothing".

**There is no schedule to fill in.** The tool asks ESPN which scoring
periods belong to each matchup period and reads the answer from the
response, and the dates come from MLB's own published season start via the
free public MLB Stats API. Nobody types a calendar.

ESPN season-long points uses one season-spanning reporting container rather
than weekly opponents. The tool recognizes the measured ESPN format and reads
its day-specific rosters, so an active first-year league does not have to wait
until the season ends before it can produce an almanac.

## 7. Run it

Run this from the same PowerShell window, still inside the extracted almanac folder. If you closed it, reopen that folder in File Explorer, type `powershell` in the address bar as in step 3, and press Enter.

```powershell
.\.venv\Scripts\python.exe tools\create_public_almanac.py
```

That is the whole run. It downloads every season of your league's history,
builds the almanac on your own machine, then creates the Google Sheet.

**What to expect while it works:**

- **It can take a long time** -- potentially a very long time for a league
  with ten or more seasons. It downloads each season separately.
- PowerShell will print a great deal of technical progress. Streams of
  unfamiliar text are **normal** and are not errors.
- **Do not close the window** just because one season is slow. Output that
  keeps appearing means it is still working.
- If it **stops** and you get your prompt back with an error message, it
  has genuinely stopped. Go to *If it stops* below.
- **The Google browser window does not appear until the downloading and
  building have finished successfully.** If a season fails, the run stops
  before any Sheet is created -- deliberately, so you never get an almanac
  with a silent hole in it.

## 8. The Google prompts, in order

1. A browser window opens asking you to **choose a Google account**. Pick
   the one that should own the finished Sheet.
2. It asks you to allow **one** permission, `drive.file`. That permission
   only lets the app see files it created itself -- it cannot list, open,
   or read anything else in your Drive.
3. **You may see a warning that the app is not verified** while Google's
   branding review is pending (see the note below). This guide will not
   tell you how to click past a security warning. If it makes you
   uncomfortable, stopping and waiting for approval is a perfectly
   reasonable choice.
4. The Sheet is created **private** and the almanac is written into it.
   Nothing is shared yet.
5. Back in PowerShell, just before anything is shared, the tool describes
   the league and member information the Sheet contains and asks you to
   type **`YES`**. Type exactly that and press Enter to share it.
   **Typing anything else leaves the Sheet private** -- which is a valid
   answer, not a failure.
6. After `YES`, it sets the Sheet to **anyone-with-the-link** *viewer* --
   anybody holding the link can open and read it, and nobody can edit it
   -- then reads that permission back from Google to confirm, and prints:

   `Your almanac: <link> -- share-ready.`

**That exact line is what success looks like.** If you do not see it, the
Sheet was not shared. You still get its URL and a plain sentence about
what went wrong, and your Sheet is never deleted to tidy up.

**Check the link before sending it to your league.** Open it in a private
/ Incognito window, or while signed out of Google. If it opens there, it
will open for them.

Later runs reuse the Google sign-in stored by Windows Credential Locker
and do not normally open a browser. This release refuses to fall back to a
plaintext file if Credential Locker is unavailable. A legacy
`output/.sheets_public_oauth_token.json` is migrated only after a secure
write is verified, then its plaintext copy is removed. The workbook
ledger, your data files and your ESPN cookies stay ordinary local files
protected by OS/file permissions and `.gitignore`. Nothing is uploaded
anywhere except the almanac you asked to be written (or your own Snowflake
account if you deliberately choose the advanced path). Revoke the Google
permission at
[myaccount.google.com](https://myaccount.google.com/permissions) --
whenever you choose, since nothing here expires it for you.

### The caveat: branding review is still pending

The app's Google sign-in configuration is **In Production**. It
is not restricted to a test-user list, and the week-long grant expiry that
applies in Google's testing mode does not apply to it. The application's
homepage, Privacy Policy and Terms are live at
[kpdawson.com](https://kpdawson.com). `drive.file` is a non-sensitive
scope, and Google reports that data-access verification is not required
for it.

What is still outstanding is **branding verification**. It has been
submitted and Google's review of it has not come back. Until that is
approved, Google may withhold the configured branding and may show an
unverified-app warning on the consent screen.

That warning is about branding review, not about what the app can reach:
it still requests only `drive.file`, so it can see the workbook it creates
and nothing else in your Drive. If the warning makes you uncomfortable,
waiting for branding approval is a perfectly reasonable choice, and this
guide is not going to walk you around it.

If you would rather use your own OAuth client -- maintainers, and anyone
working from a clone or testing against a different Google project --
that route is an advanced override, described in
[SETUP.md section 10](SETUP.md#10-optional-google-sheets-sink). It is not
a prerequisite for anything above.

## If it stops

Something failing here is expected at this stage -- this path is young and
has not been walked by many people. A good report is genuinely useful.

**Do this:**

- **Leave the folder alone.** Do not delete it or start over; the state
  that failed is the useful evidence.
- **Do not go run other commands at random.** If the error text tells you
  to do something specific, do that. Otherwise stop.
- **Copy the command you ran and the last chunk of error text** into the
  structured GitHub bug-report form after sanitizing both.

**Before you send anything, take these out:** your ESPN cookies, your
league id, your league members' names, the Sheet URL, and your Windows
username or file paths where you can. A screenshot is fine **only after**
you have checked it contains none of those.

**Never attach** your `.env` file, any Google token or Credential Locker
contents, the downloaded raw data, the `.duckdb` or `.parquet` files, or
the workbook ledger. None of them are needed to diagnose anything.

Open the [GitHub issue chooser](https://github.com/KyleDawson24/fantasy-league-almanac/issues/new/choose)
and choose **Report a bug**. If the report cannot safely be public, use
**kpdawson.github@gmail.com** as the private fallback. Do not send secrets
or private data files through either route. The complete redaction and
triage policy is in [Reporting a problem or requesting coverage](docs/reporting-an-issue.md).

## What you get

A Google Sheet in your own Drive with a link you can paste into your
league chat.

`out/almanac_preview/` also holds one tab-separated file per almanac tab
when you render previews: the records, the standings, the matchup history,
a draft recap, and a page per team. Open them in anything.

`.\.venv\Scripts\python.exe output\generate_summary.py` prints a weekly
recap as BBCode, ready to paste into a league message board.

## Advanced: running the stages by hand

**You do not need any of this for the path above.** Step 7 runs these for
you. They are here for troubleshooting a failed step, for rebuilding one
stage without redoing the others, and for the weekly loop once the first
build exists.

Every command runs from the project folder, on the local (no-warehouse)
path:

```powershell
.\.venv\Scripts\python.exe extract\extract.py --raw-target local --include-settings --include-transactions
```

```powershell
.\.venv\Scripts\python.exe tools\load_parquet_to_duckdb.py
```

```powershell
.\.venv\Scripts\dbt.exe deps --project-dir dbt_league --profiles-dir dbt_league/profiles
```

```powershell
.\.venv\Scripts\dbt.exe build --project-dir dbt_league --profiles-dir dbt_league/profiles
```

```powershell
.\.venv\Scripts\python.exe output\generate_almanac_sheet.py --duckdb --no-sheets --preview-dir out/almanac_preview
```

The first command writes parquet under `data/parquet/raw/`; the second
loads it into `data/duckdb/ESPN_FANTASY.duckdb`. `--include-settings` and
`--include-transactions` are needed on the FIRST run only -- afterwards
plain `.\.venv\Scripts\python.exe extract\extract.py --raw-target local`
picks up recent weeks. `dbt deps` is also first-run only.

ESPN sometimes authorizes ordinary league reads while refusing that same
member access to the separate communications feed that carries the durable
transaction log. That is not treated as “no transactions”: the run continues,
the warning names the unavailable feed, and transaction-dependent blocks are
omitted or marked unavailable. The standings, box scores, records and other
league output still build.

A complete season-long-points history makes hundreds of small ESPN requests.
If ESPN resets a connection, times out, throttles the request or returns a
server error, the command retries that one request three times with short
waits. You may see a `[retry]` line and normally need to do nothing. Bad
credentials and other permanent client errors are not retried, and a request
that still fails after the bounded retries stops before writing that day's
partial data.

`dbt build` is correct here, and it is what step 7 runs. It is clean on
the installation described above -- every `league_config` file left blank,
nothing but ESPN data in RAW. All 20 seeds load and the CBS-side models
come out empty, which is the true thing to say about a league with no CBS
configuration. That is checked by a test rather than by memory, because it
was briefly untrue: an empty CSV has no data to infer a column type from,
so a few CBS columns used to arrive as the wrong type and take three
models down with them. The types are declared explicitly now.

If you already have a local build and only want the Sheet, the
lower-level command is `.\.venv\Scripts\python.exe
output\generate_almanac_sheet.py --new-public-workbook`; it also forces
DuckDB unless `--advanced-snowflake` is present.

The Snowflake equivalent of the stages above, for maintainers, is in
[SETUP.md section 8](SETUP.md#8-first-run).

## What this path does not cover yet

Said plainly, because finding out later is worse:

- **Configuration is still manual.** Steps 5 and 6 are files you edit.
  There is no installer that asks you questions and no `.exe` yet; a
  form-driven setup is planned for a future release.
- **The first untouched-machine, new-league rehearsal is in progress.** It
  has reached real extraction and already exposed issues that were fixed,
  but it has not yet produced the final workbook. This guide is therefore
  still careful rather than fully proven.
- **ESPN leagues only.** The CBS side of this project is real and ships
  in the almanac, but its ingestion runs on browser-extracted
  credentials behind a reCAPTCHA login, so there is no scripted setup for
  a CBS league and it is not part of this journey. Tracked publicly on
  the roadmap.
- **Private leagues only, in practice.** The supported entry requires the
  ESPN cookies from step 5. A public league is not proven through this
  command.
- **Recent seasons come through at full fidelity.** Older ones depend on
  what your platform still serves. This repo reconstructs a
  quarter-century for its own two leagues, and the honesty notes about
  where that reconstruction is estimated rather than recorded are in the
  [README](README.md#the-two-league-story) and
  [Known Data Issues](docs/known-data-issues.md).
- **Settings, transactions and the draft are current-season only.** ESPN
  serves those for the CURRENT season alone, so earlier seasons arrive
  with their box scores and matchup calendar but not those three feeds.

  Three things that sound alike and are not. An ordinary run captures the
  **current** season's weeks by itself. `--matchup-schedule-only --year
  2025` captures **2025's weeks only**, in one request. `--all-seasons
  --matchup-schedule-only` captures the weeks for **every season your
  league registry bounds** -- and only the weeks: it downloads no
  historical box scores, which are the slow part and still go a season
  at a time.

- **ESPN season-long points is supported from the middle of its first
  season.** A real ESPN league measured this format as league type 5 with
  one season-long multi-team period. The extractor reads each reportable
  scoring day and its fantasy rosters without inventing weekly opponents or
  wins and losses. **Rotisserie remains unproven** and still refuses rather
  than guessing.
- **A league that drafted late still counts the whole MLB season.** This is
  the one number-affecting limitation in v1.9, so it is spelled out rather
  than left to be discovered.

  The extractor walks every scoring day of the baseball season. If your
  league drafted in June or July, the months of MLB production **before
  your league existed** are still counted, and each player's share is
  credited to whichever team first rostered him. The rehearsal league
  drafted on July 31 and began counting on August 1, so roughly four
  months of every season total came from days nobody managed.

  What that does and does not distort:

  - **Comparisons between your teams stay fair.** Every team carries the
    same pre-league days, so the standings order, the boards and the
    rivalry comparisons are not tilted toward anyone.
  - **Absolute totals are inflated.** Season points, career points, the
    record book and draft-value deltas are all larger than what was
    actually managed in your league.

  The workbook says so itself: a league that drafted more than two weeks
  after opening day gets a plain-language warning on Home and on Advanced
  Standings, naming the draft date and how many days it trailed the
  opener. Nothing is silently adjusted.

  A future release will treat pre-league production as its own category --
  neither active nor inactive -- rather than filtering it away. That
  correction touches extraction, the facts and every aggregate at once, so
  it is deliberately not bolted on here.
- **The unfinished current matchup in a weekly H2H league is still
  excluded.** Closed matchups update normally; showing a live matchup day by
  day is a separate enhancement, not part of the season-long-points fix.

## Something hurt?

This path is young. It has been walked by the person who wrote it and not
many others, which is exactly the kind of testing that misses things.

If a step was wrong, ambiguous, or assumed something you did not have,
say where you got stuck -- see *If it stops* above for how to report it
without leaking anything. That feedback is what 2.0 is for, and a report
that says "step 3 made no sense because X" is more useful here than a
patch.
