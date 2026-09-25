# Release Notes -- v2.2.0

The almanac gets a website. 6 commits since v2.1.0.

v2.1 was about the weeks after the first run. v2.2 is about a second way to read the same book. A live, anonymized demo dashboard now reads the almanac's data in the browser, and the data half that feeds it lands in this repository: one shared export written once for both the Sheet writer and the browser, and all-play promoted from a one-off query into a tested mart. Around that, the byte-diff corpora stop drifting with the calendar, the local DuckDB lane renders the CBS book again, and the maintainer's checkout gains two layers of loss prevention.

One boundary first, because it is the easiest thing to get wrong about this release: **everything in this repository builds the Google Sheets almanac. The dashboard is a separate web layer over the same data, and its site code is not in this repository.** Cloning or downloading v2.2.0 gets you the pipeline and the Sheets almanac, not the website.

Minor rather than major: everything here is additive, no golden moved, and an existing install has no migration step to run. The guided Windows launcher and setup wizard are byte-identical to v2.1.0, so the clean-machine rehearsal was not repeated; the evidence is under **Upgrading**.

---

## The almanac has a website

The headline. **[The demo dashboard](https://kpdawson.com/almanac/demo/)** is the ESPN demo league with fictional club and owner names and real MLB production, and it runs entirely in the visitor's browser: DuckDB-WASM over published Parquet files, no server and nothing to install. It covers standings, team pages with the lineup drawn as a diamond or as a table, player statistics, and filters that recompute in the page rather than fetching a pre-cut answer.

It is an early preview, and it says so on the page. The first load on a slow connection can take several seconds while the engine and the Parquet files arrive. Performance work is scheduled for 2.3.

What ships in this repository is what the dashboard reads, described next. The site that draws it does not, yet.

## One export, two consumers

The data-engineering story of the release (MLB-301).

Until now the only consumer of the standings marts was the Sheet writer, and a number's meaning lived partly in the writer's Python: which denominator it was divided by, how many decimals it printed, whether green meant high or low. A second consumer would have had to re-derive all of that, and two re-derivations drift.

`output/export_shared.py` writes, once per almanac update, one file per table under `data/exports/shared/<league>/<snapshot>/`: detailed standings per matchup and in totals, matchup history, points by lineup slot, acquisition channels, season finishes and their summary, affinities, rivalries, the all-play mart and its per-scope summary, and a team spine. Every applicable row carries the printed number **beside** its raw total, the denominator kind and value, the display precision, the scoring lens and the polarity. The printed value is produced by the writer's own helpers, so nothing is reverse-calculated from a rounded average and no Sheet cell is ever read as a source. Today's two denominators are kept exactly as the pages use them: scoring days for Detailed Standings, matchup periods for Points by Lineup Slot. Acquisition channels export no denominator because none exists.

Two files ride alongside the tables. `manifest.json` records the snapshot, the performance cutoff, the per-season horizon, club identity as-of kept separate from performance, the scoring-rule version, row counts, the source commit and the fixture stamp. `presentation.json` is **generated** from the Sheet writer's own gradient functions and the league weights, not hand-written beside them, so the browser colours a column the same direction the Sheet does for the same reason.

Reconciled against the unformatted Sheet capture at the Sheet's precision: 32,972 cells, with every difference traced to raw data that landed after the frozen week-22 input.

**All-play becomes a mart.** Expected record used to live in a one-off season-wrap query. `mart_team_all_play` has one row per completed regular-season team-matchup: the team's platform score set against every other team's that period, with strict wins, strict losses and ties counted separately, plus the opponent count. Carrying the opponent count is what lets expected wins sum honestly across seasons in which the league changed size. Ties stay separate; there are no half-wins. Playoff periods are out, and a period counts only under the same fail-closed closure gate the rivalry ledger uses, so a week in flight is never a result. Two singular tests pin it: league-wide reciprocity, with the opponent count equal to teams minus one, and no playoff or open rows. A pytest runs the model's SQL over a synthetic league that covers a four-team season, a six-team season with an exact tie, a playoff week, an open week and a second league sharing ids. The export is registered as a dbt exposure, so the lineage graph shows the new consumer.

Existing marts and renderers are untouched. No Sheet changed.

## Goldens that move only when logic moves

The v2.1 cut recorded four golden failures that were data drift, not regressions: the corpora rendered from the live warehouse, so every week that landed moved them. That is a tripwire that goes off on the calendar, and one people learn to ignore.

Since MLB-295 the ESPN head-to-head and CBS corpora render from a **frozen week-22 fixture**: private, checksummed raw inputs plus projected league configuration, rebuilt into a separate local DuckDB file only when warehouse logic or adapter versions change. The harnesses never open the live warehouse, and a byte difference reports the affected tab and line numbers. The re-anchor that set this up records its input checksum and local build version in the tests; the reviewed differences from 2026-08-31 were later-captured data and documented historical engine discrepancies, with no untraced lines. The separate points rehearsal corpus keeps its existing input and goldens.

The result at this cut: the frozen ESPN and CBS corpora were byte-still, and no golden was re-anchored. Said plainly, because it is the same trap in a smaller place: two older goldens -- the BBCode weekly summary and the BBCode records report -- were not moved onto the fixture and still render from the live warehouse at its latest matchup period. They were anchored at matchup period 18 and the warehouse now holds period 24, so both drifted for data alone, exactly as they did at the v2.1 cut. They were left un-anchored rather than moved to a new baseline. So was the one other warehouse check that failed here, the one that notices two backup tables the raw-schema contract does not list, as it did at v2.1. Moving the BBCode pair onto the fixture is the obvious next step.

## The DuckDB lane

Both union branches of the daily fact and the team-owner dimension now cast to explicit semantic types: numeric ids and counts INTEGER, points and weights DOUBLE, platform player keys VARCHAR, and eligibility as JSON on DuckDB or ARRAY on Snowflake. Rate operands stay DOUBLE, so division stays floating-point. DuckDB 1.5.5 still rejected two grouped top-N queries after the casts; their small aggregates are now materialized before ranking rather than disabling optimization application-wide, and the CBS book renders all 21 tabs locally, twice, byte-identically. The visible side effect is representation only, such as a counting stat losing a trailing `.0`.

Disclosed rather than implied: this release does **not** claim Snowflake-to-DuckDB parity. The Snowflake full refresh and the two-lane comparison were deliberately deferred to one consolidated validation, and the default-lane flip is not in 2.2.

## Loss prevention

This project keeps its most valuable files out of git on purpose (the real league configuration, the goldens, private notes), and git protects only what is committed and pushed. Two layers now cover that gap (MLB-300).

- **An agent deny list.** The tracked `.claude/settings.json` carries a `permissions.deny` list for both the Bash and PowerShell tools that refuses working-tree wipes (`git clean`, `reset --hard`, `checkout -- .`, `restore`, `stash`), every push, ref deletion and history rewriting, recursive deletes in every shell, and `robocopy /MIR` or `/PURGE`. Deny rules are evaluated before allow rules and in every permission mode. `.claude/README-deny.md` explains why, and describes the move-into-a-holding-folder convention that replaces deletion. A clone inherits both.
- **Nightly snapshots.** `tools/backup_snapshot.ps1` bundles every ref, copies the untracked files into dated folders, takes a weekly raw dump, and mirrors `main` only when the tree is clean and the push is a fast-forward. It never deletes: retention moves expired snapshots aside. Credentials and warehouse copies are excluded from every copy.

Neither changes what the pipeline builds. They are here because they are part of how the project is run, and it says so.

## Known limitations

- The dashboard's first load on a slow connection can take several seconds. Performance work is scheduled for 2.3.
- The dashboard's site code is not in this repository. The shared export and the all-play mart are.
- Snowflake-to-DuckDB parity is not claimed (see **The DuckDB lane**).

## Explicitly not in 2.2

The dashboard's site code; the CBS dashboard, which follows after this cut; the DuckDB default-lane flip (MLB-284); the warehouse declared-pass riders; and the broader Wasted definition. The dashboard design document (MLB-298) stays a living document rather than a shipped specification.

---

## Upgrading

Nothing to run. There is no migration step, no seed to fill and no config key to add.

Two things an existing install may notice:

- **A new model builds.** `mart_team_all_play` and its two singular tests join `dbt build`. It reads marts you already have and needs no configuration.
- **`output/export_shared.py` is opt-in.** Nothing in the weekly chain calls it; it writes under `data/exports/shared/` only when run.

The guided Windows journey is unchanged. `START_ALMANAC.cmd`, `ROTATE_ESPN_CREDENTIALS.cmd`, `tools/windows_launcher.py`, `tools/setup_league.py`, `tools/build_release_bundle.py`, every file under `config/` (including `bootstrap.py`, `bootstrap_runner.py` and `bootstrap_writer.py`), `output/sheets_auth.py`, `output/public_oauth_client.py` and `requirements.txt` are byte-identical to v2.1.0: `git diff v2.1.0..v2.2.0 -- <those paths>` is empty. The v2.0 clean-machine rehearsal therefore stands for this release and was not repeated.

The suite at this cut is **1915 pure tests** and **786 dbt data tests** over **104 models**, collected on a fresh clone.

Full detail in [CHANGELOG.md](CHANGELOG.md).
