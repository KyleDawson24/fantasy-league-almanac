# Release Notes -- v2.2.1

The dashboard's data layer comes home. 42 commits since v2.2.0.

v2.2 gave the almanac a website. v2.2.1 closes out the season that website launched in. The data layer every dashboard edition is now published from lands in this repository, as one writer for both league formats rather than a fork per platform. The public sample workbooks are re-cut through the end of the 2026 season, and the anonymizer that makes them stops re-checking its own work and starts looking for everything that could be wrong. The project site gains a dashboard guide and a setup page.

The same boundary as v2.2.0, because it is still the easiest thing to get wrong about this project: **everything in this repository builds the Google Sheets almanac. The dashboard is a separate web layer over the same data, and its site code is not in this repository.** What is new here is the half of the dashboard that is data: the writer that publishes a league's extract, the shared tables built from it, and two models it reads.

Patch rather than minor: the Sheets almanac a clone builds renders the same pages from the same configuration, no golden moved, and an existing install has nothing to migrate; what is new serves the dashboard editions, which ship on their own cadence.

---

## One data layer for every league format

The headline (MLB-319). v2.2.0's dashboard read a pre-cut export shaped for one ESPN head-to-head league. Bringing a CBS season-points league onto the same site would have meant a second writer, and two writers drift. `dashboard/publish_extract.py` is one writer for any league: a day-grain atom, one row per player per scoring day, plus the dimensions a page needs. A new league is a data-source swap, not a fork.

What decides behaviour is the league's **format**, never its platform's name. A head-to-head league's periods are its dated matchups. A season-points league has one matchup per season, against every other team, and its scoring periods are bins: CBS periods are derived as Monday-to-Sunday weeks after an opening stub, then validated against the periods CBS delivered, by count and by content, and reported rather than adjusted. Only 2026 has delivered periods to check against. 69 of its 416 team-period totals agree exactly; the rest is a known level gap from pitcher start limits the warehouse cannot see, and the manifest says so.

Beside the atom, the writer publishes:

- team-grain matchups for every format, checked against the invariant that makes the two formats comparable: wins, losses and ties sum to the number of other teams for a season-points matchup, and to one for head-to-head;
- the rivalry ledgers and the platform's delivered period standings, passed through as they are, so no second implementation of franchise lineage or official ranking exists downstream;
- the stat vocabulary, franchise identity, each team-season's owner label, and each team's name over the dates it carried it;
- **whether each season is over**, as a computed fact with its evidence, never a hand flag. A head-to-head season is over when its last scheduled matchup, playoffs included, has ended inside the data horizon; a points season when the platform has delivered standings for its declared last period;
- **outfield games by spot**: per player-season, games at LF, CF and RF from the MLB spine's per-game positions, so a league whose lineups say only OF can still seat its outfielders on the diamond. An ambiguous identity is left out rather than seated by someone else's games.

The flex hitting slot (ESPN's UTIL, CBS's U) now takes any hitter, as it does in play. Whether a league's DH does too is a league rule rather than a platform one, so the league registry gains an optional, validated `settings_overrides.open_hitting_slots`.

The run is careful with what it touches. The warehouse is attached read-only. Each run writes a new stamped directory, and nothing is deleted or overwritten. Every season file is checked for row parity with the fact and for uniqueness before the run continues. A guard refuses any column or value shaped like an email address or a phone number. The manifest records the source commit, the data horizon and a sha256 per file.

`dashboard/shared_from_neutral.py` rebuilds the shared almanac tables (standings, slot points, finishes, all-play, rivalries) from one league's extract, through the shared export's own shapers, so row shapes and printed values are the export's. Only a closed season crowns a champion. The old export marked four 2026 teams champion while the playoffs were still running; with the completeness fact, a finished season's champion appears once its final has been played.

`dashboard/convergence_check.py` is the evidence that moving a page from the old export onto the new layer moves no number. It recomputes Detailed Standings, Points by Lineup Slot and Season Finishes from the atom and dimensions alone and compares them cell by cell, at the warehouse's own rounding grain. On the 2026-09-20 horizon the ESPN league converges exactly: 1,748 of 1,748 standings cells and 612 of 612 slot cells.

The live dashboard editions were published from exactly this code: the writer and the shared-table builder on `main` are byte-identical to what produced them.

## Two new models

The dashboard needs two things the daily performance fact does not carry: a player's whole real-life stat line for a day, including the discipline the lineup slot did not credit, and every day the player was rostered, game or not. Both used to exist only in ESPN-shaped relations.

- **`fct_player_daily_production`** is that stat line in one shape for every league: from the platform's own per-day breakdown where one exists, otherwise from the MLB game-log spine, so each half of a split two-way player takes only its own discipline.
- **`fct_player_roster_day`** is the roster ledger: the platform's daily roster snapshot where one exists (for CBS, the 2026 daily capture), otherwise observed game days, then inferred gaps between two game days on the same team, then resolved add/drop stints, with the most direct evidence winning. It carries each day's display name, MLB club, and the platform's own player name beside the league's display name, so an edition chooses which one it publishes.

Neither model branches on a platform name. Built and tested against a DuckDB copy of the Snowflake models, the ESPN side reproduces the published dashboard's player file on all 136,599 rows and its roster file on all 164,682. The CBS side covers all 989,033 fact rows and matches the fact's credited components exactly except one no-hitter (61 against 60). CBS's 2026 roster days are now all observed: 34,965 game days plus 51,435 snapshot days, the capture's full 86,400.

## A fix in the shared export

v2.2.0's shared export died on the CBS league with `invalid identifier 'IRSTR'`. CBS scores IRSTR, but the team-grain marts' stat columns were built from ESPN's vocabulary and have no column for it; its points ride inside the pitching total. The export now asks only for the stat columns the target relation actually has, through the information schema, so it reads the same on Snowflake and DuckDB. ESPN is unchanged. A test runs the real SQL against a throwaway DuckDB warehouse shaped like the marts.

## The anonymizer looks for what could be wrong

The public sample workbooks are copies of real league renders with the people swapped for fictional twins (MLB-323). The tool that does the swap used to audit its result by rescanning the copy for the real forms it had replaced. That can only confirm the scrub did what it set out to do: a form it dropped, or never knew about, was invisible by construction, and the sample refresh found both.

`tools/anonymize_sheet_copy.py` now:

- builds its audit inventory from the real side alone (owner names, co-owner labels in both spellings the warehouse and the renderer produce, owner ids, contact details, the private map's forms) and searches every cell, formula and tab title for all of it;
- requires `--league`, so the inventory is that league's;
- maps a co-owner label whole, before any first name inside it;
- matches team abbreviations case-sensitively, on word boundaries, with each pair scoped to its own league;
- refuses before its first write when a real owner has no reviewed twin, because a twin is a reviewed row and the tool must never invent one;
- prints the Drive revision-history steps after a clean run, because a zero-residual audit speaks for the cells, not for the file's version history.

The principle, ruled during this release: **the anonymizer hides people, never MLB players or team names.** Players are public figures and are matched generously. Fantasy team names and abbreviations are public content, counted as information and published as written, unless a team name carries a real owner's first and last name, which makes it an owner form.

## The site and the samples

- Both public sample workbooks, the head-to-head league and the points league, are re-cut through the end of the 2026 season and re-linked from the README, the homepage, the new setup page, the dbt exposures and the regenerated technical catalog (MLB-321). The README now says what "anonymized" means there: owner names and labels become fictional twins, team names stay as written unless they name a person, and MLB player names, stat lines and every number are real.
- The project site gains a **dashboard guide** and a **setup page**, and the homepage is rebuilt around real workbook views.
- The homepage's "faster loads are planned for 2.3" caveat is retired by measurement (MLB-309). On the project's Chromebook proxy (Chrome DevTools, CPU throttled 4x, no network throttle) **[the demo dashboard](https://kpdawson.com/almanac/demo/)** shows its headline in about 0.4 s and its lineup diamond in about 1.1 s, inside the 3-second bar. A slow mobile connection is a different matter; see **Known limitations**.

## A second consented edition

The decision record that lets one league, the maintainer's own, show its members' real names on its own unlisted dashboard gained Amendment 1 on 2026-09-27 (MLB-308, `docs/decisions/REAL_NAME_PUBLICATION_EXCEPTION.md`): a second league's commissioner consented on behalf of its members, on the same terms. That edition is published from the code above, at an address handed to the league and linked from nowhere public, these notes included. Email addresses and phone numbers stay off both editions. Every other league publishes only as name-anonymized twins.

## Known limitations

- **Slow connections.** Under Chrome DevTools' Slow 4G profile the dashboard's headline takes 5.4 to 7.2 s and the lineup diamond 15 to 17 s, on every edition, because of the boot request chain. That work is not in this release.
- **Two BBCode goldens drift with data** (MLB-111). The weekly-summary and records-report goldens still render from the live warehouse and were anchored at matchup period 18; the warehouse now holds the complete 2026 season, so both fail for data alone, as they did at the v2.1 and v2.2 cuts. Moving them onto the frozen fixture is the fix.
- **The raw-schema contract check fails on backup tables** in RAW that the contract does not list (MLB-326), as it did at v2.2.0.
- **League filtering on config seeds is not yet audited** (MLB-328): an audit that every read of a config seed carries a `league_key` predicate is open.
- **An orphan tab after a rename** (MLB-324): when a team's tab is renamed, the Sheets renderer leaves the old tab behind.
- The dashboard's site code is not in this repository. The data layer it reads is.
- This release does **not** claim Snowflake-to-DuckDB parity either; nothing here changes the v2.2.0 position.

## Explicitly not in 2.2.1

The dashboard's site code, and any work on the boot request chain that slow connections wait on.

---

## Upgrading

Nothing to run. There is no migration step, no seed to fill and no config key to add.

Three things an existing install may notice:

- **Two new models build.** `fct_player_daily_production` and `fct_player_roster_day` join `dbt build` with their schema tests. They read models you already have and need no configuration; a league without a daily roster capture falls back to observed game days and transactions.
- **The league registry accepts one new optional key**, `settings_overrides.open_hitting_slots`, set for the CBS league in the tracked `config/leagues.yml`. It is validated at load, so a misspelled key under `settings_overrides` fails loudly instead of being ignored. A registry without `settings_overrides` is unaffected.
- **The `dashboard/` scripts and the dump tool's new flags are opt-in.** Nothing in the weekly chain calls `dashboard/`. `tools/dump_snowflake_raw_to_parquet.py` gains `--schema ANALYTICS`, `--keep-going`, `--where` and `--out-dir`, all read-only against Snowflake.

The guided Windows journey is unchanged apart from those two registry files. `START_ALMANAC.cmd`, `ROTATE_ESPN_CREDENTIALS.cmd`, `tools/windows_launcher.py`, `tools/setup_league.py`, `tools/build_release_bundle.py`, `output/sheets_auth.py`, `output/public_oauth_client.py`, `requirements.txt`, and every file under `config/` other than `league_registry.py` and `leagues.yml` are byte-identical to v2.2.0: `git diff v2.2.0..v2.2.1 -- <those paths>` is empty.

Clean-machine rehearsal: exercised on a fresh venv (Python 3.13, a `git archive HEAD` export with no `.git`) through the guided launcher's first credential prompt and the default test suite: 1881 passed, 38 deselected, 4 expected failures (no league data and no release-notes file in the export) and 20 errors in one module that needs a git checkout (`test_weekly_chain_without_seed`; a clone runs it clean). The credentialed ESPN, Google and Snowflake steps were not rehearsed for this patch. The two changed `config/` files are `config/league_registry.py` and `config/leagues.yml`.

The suite at this cut is **1973 pure tests** and **797 dbt data tests** over **106 models**, collected from the tracked tree.

Full detail in [CHANGELOG.md](CHANGELOG.md).
