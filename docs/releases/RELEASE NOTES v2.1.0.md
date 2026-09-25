# Release Notes -- v2.1.0

The season-long release. 47 commits since v2.0.1.

v2.0 was about the first run: a stranger with an ESPN league double-clicks a launcher and ends up with a workbook their league can open. v2.1 is about the weeks after that. Two surfaces the league actually asked for land on both almanac workbooks, one attribution bug that moved rendered numbers is fixed, the build fails loudly in three places where it used to under-count quietly, and the transform layer's two platforms now converge on shared dimensions and facts with no rendered byte moving.

Minor rather than major: everything here is additive or corrective, the release spent exactly one declared golden pass (2026-08-31), and an existing install has no migration step to run. The guided Windows launcher and setup wizard are byte-identical to v2.0.1, so the clean-machine rehearsal was not repeated; the evidence is under **Upgrading**.

---

## The Records Page Redesign

The headline. The old Records tab was one long platform-specific page. It is replaced by a Records book of three surfaces -- **Lifetime Records**, **Season Records** and, for head-to-head leagues, **Matchup Records** -- rendered by one shared engine on both the ESPN and CBS workbooks.

The format decides the tabs and the bands, not the platform. A points league gets no Matchup tab because it has no matchups; a head-to-head league gets all three. Within each surface the record rows are driven by the league's own settings: the stats it scores and the lineup slots it fields become the rows, so a league that scores holds gets a holds record and a league without a corner-infield slot never sees one. The Halls and the Top-N boards ride along in the same shape. Where a record is a mass tie the band says when it happened rather than listing forty names, and the Details column names players short so the table reads as a table.

One disclosure, because it is the kind of thing this project would rather say than hide. For the CBS league, seasons before 2026 have no daily sit/start capture, so hitter slot records at season grain lean on the same position-eligibility **estimates** the standing page already used. Those rows are marked estimated, and every CBS tab carries a linked legend row pointing at the user guide's "How accurate is the old stuff?". The Best Weeks band keeps actual slots only. A disclosed estimate beats a false blank; a blank would have implied the league had no record there.

Two things to know about how it refreshes. The new tabs are written by their own command (`python output/generate_records_book.py --league <key> --prod`) against the league's configured Sheets sink, and the standing weekly renders do not refresh them yet; the guided Windows journey does not render them at all. Wiring the Records book into the standing writers is MLB-283 and rides 2.2. In the meantime both writers agree on where the tabs sit -- directly after Advanced Standings -- which they did not for a few days in September: each renderer's tab-sort pass knew only its own titles, so every weekly render pushed the three tabs behind the appendix and the records command parked them back in the hidden legacy Records slot. Fixed on both sides; only the tabs a workbook actually carries move, and a title the book lacks no longer leaves a gap.

## Roster eligibility on Advanced Standings

The first feature in this project's history that a league member asked for, and it shipped the same day the request came in.

Per team and position, how many players on the **current** roster are eligible there. It sits immediately under Points by Lineup Slot and mirrors its shape on purpose: the two answer adjacent questions -- what a manager did with his slots, what he could have done with his roster -- and reading them against each other is the point. Both books carry it; the ESPN season-points workbook picks it up through the same builder the CBS book uses.

Only positions the league actually fields render, chosen by the same seats-not-settings rule the deployment grid uses (ESPN enumerates every slot the platform offers and gives the unused ones zero starters). The flex columns stay, and the reason is recorded on the model because the simplification is tempting and wrong: eligibility is earned per position by games played, and a flex slot earns separately on the combined total, so a player with ten games in each outfield spot is eligible at OF and at none of LF, CF or RF. The flex column cannot be reconstructed from the atomic ones -- they are all zero in exactly the case it exists to describe.

## Traded-player week attribution

The one change in this release that moved rendered numbers, and it rode the declared pass.

A player traded mid-week could have his whole week filed under the wrong team, because the weekly slot fact's recency key did not order his stints chronologically and carried no last-scoring-period marker to break the tie. Measured across the ESPN league's history, 94 of the 241 multi-team player-weeks were relabelled. The weekly fact now carries `last_scoring_period`, the recency key is chronological, and the goldens were re-anchored on review with the movement decomposed and confined to the surfaces the fix touches.

A smaller one in the same family: the Team of the Month board priced a pitcher who reached it through his SP or RP eligibility as a hitter, which for a pitcher is zero, and then dropped him for scoring nothing. A league that configures SP and RP seats had no pitchers in the two slots pitchers occupy. The daily-window path was the one copy of that CASE that never got the three-slot spelling the season-grain fact has always had. No golden moved, because the rehearsal league fields seven generic P seats; the fix is carried by a test that asserts on the emitted SQL, since a stubbed pool cannot execute the CASE.

## Stranger-proofing: loud where it used to be quiet

The league-shape audit (MLB-222) went looking for every place the two pioneer leagues had been baked in as the shape of all leagues. Most findings were dispositioned as bounded; four were the silent-wrongness class and are now build failures with a name.

- **A scored stat the vocabulary cannot name fails the build.** The scoring bridges are inner joins onto the stat seeds, which is the right shape for the models and also makes an unknown scored stat disappear: the league scores it, the feed carries it, the staged rules do not, and every calculated total downstream is short by that stat's points with nothing to say so. Two singular tests now list every scored stat the seed does not carry, for ESPN and for CBS. Vacuous for a league with no feed, green on both pioneer leagues, and they fail **by id** on a stranger's league -- the fix is a seed row, which is where every other stat lives.
- **Hitting plus pitching must equal the total on every daily row.** The strict-slot filter deliberately lets fielding stats through for any slot, so a league scoring errors or assists would carry those points in the total and in neither subtotal. Measured on the live warehouse at zero rows off by more than 0.001 across 1.1 million ESPN and CBS rows; the day a league first scores a fielding stat this fails on the first build, which is the moment to decide where those points belong.
- **The CBS scoring-seed assert is scoped to the league it documents** (MLB-219). The seed's 2026 scoring columns are one league's hand-curated configuration, kept so the test can catch the feed drifting from the documentation. Grading a stranger's correct feed against them would produce a wall of confident false failures at exactly the moment a new user is deciding whether the project works. A new project variable names the curated league; every other league is out of scope by construction. Measured first: the ESPN-only build the public entrypoint runs already passed this test vacuously, so this is hygiene rather than an emergency.
- **The incremental watermark compares two columns, not a packed number.** The old key was `season_year * 100 + matchup_period`, which assumes a season never has more than 99 periods. A league that plays daily matchups has about 186, and there the first weeks of every new season sat below the watermark and were silently skipped on incremental runs. Proven by sweeping both predicates over 256 watermark points on DuckDB: identical at 17, 26 and 99 periods per season, and at 186 the packed form skipped 10,816 row-selections that the two-column form keeps.

## Reliability

**The DuckDB segfault gets a bounded, loud retry.** `stg_mlb__player_game` crashes nondeterministically on the local DuckDB lane (exit 139), and the wrapper used to stop on its startup-failure branch. A ten-attempt rate trial on 2026-09-13 put the flake at roughly 40% per attempt; one retry would have left about 16% of full builds dead on it, two leave about 6%, and a crash dies inside two and a half minutes, so the second retry is close to free. `tools/duckdb_run.sh` now retries up to twice, announces every retry where it happens and again in the run summary, never retries an ordinary failure, and stops the build as a finding when the cap is exhausted. The cap is tunable through `MAX_SEGV_RETRIES`.

## Foundation, honestly labelled as invisible

The convergence program (MLB-249, then MLB-263) is the largest block of work in the release and a user cannot see any of it. The audit proved that the two platforms converged only at the daily fact and every surface above it was reconciled in platform-specific Python; the execution then landed, additive first, then re-point, then delete, with the byte-diff corpora as the tripwire at every step.

What now exists once for both platforms: a lineup-slot dimension with a current-roster eligibility mart; the opening-roster collapse emitted by the acquisition mart instead of derived in the presenter; a game date on every daily row, ESPN included; the MLB club id on the daily fact with `dim_mlb_team` beside it; a withheld-owner flag on `dim_owner` with the resolved label carried on the fact; one scoring-rules model (`dim_league_scoring_rule`) and one "where did each team stand after period N" fact (`fct_team_period_standing`) for both leagues; a named lens on the season-standing dimension with the CBS awarded finishes unioned in; and the CBS league reaching `dim_matchup_period` in its true season-long shape. The acquisition bridge in the renderer was deleted at a measured zero callers.

Two rulings changed how the model thinks rather than what it emits. League format is now decided from **settings**, not from the shape of the feed -- the CBS config has carried the league's rules all along and nothing had read them, and a CBS head-to-head league would have been misfiled as a points league by presence alone. And the abbreviation-disambiguation rule is written into the adapter contract (CBS has exactly two collisions, ESPN zero). Both format verdicts are published as their own columns so their agreement is a test rather than an assumption.

The docs truth-up (MLB-259, MLB-276) belongs here too: Google branding verification is approved and the docs say so, private and public ESPN leagues are both stated as supported, and sentences that describe current behaviour no longer carry a version stamp.

## A data correction on the maintainer's warehouse

Not a code change and not in the ZIP, recorded because the release's numbers depend on it. Two weekly facts on the maintainer's Snowflake warehouse were stale incrementals -- rows written before an upstream correction and never revisited. A full refresh of the two facts and their downstream cone on 2026-09-14 corrected five player-weeks and removed the orphan rows on the ESPN book, with a zero-orphan census afterwards, and both ESPN production workbooks were re-rendered from the corrected warehouse on 2026-09-15. A fresh install builds from scratch and cannot have this; an install that runs incremental builds week to week can, and the remedy is the same full refresh of the two weekly facts.

## Explicitly not in 2.1

The DuckDB default-lane flip was measured this week and the two lanes do not yet render byte-identical books, for lane-caused reasons that are now classified; it rides 2.2's declared pass with that classification as its spec. Also 2.2: the external config root, the CI DuckDB job, multi-league in one install, the recurring-updates set (the one-command weekly runner, the data-arrival gate and the MLB-layer freshness gate), and wiring the Records book into the weekly writers.

---

## Upgrading

Nothing to run. There is no migration step, no seed to fill and no config key to add.

Three things an existing install may notice:

- **`dbt build` can now fail where it used to pass.** If your league scores a stat the `stat_classification` or `cbs_stat_map` seed does not carry, the new vocabulary guard fails by id. That is the guard doing its job; add the seed row. A fielding stat in scoring trips the split-sum invariant the same way.
- **The Records book is a separate render.** The weekly chain does not refresh the three new tabs and the guided journey does not render them; an install with a configured Sheets sink runs `python output/generate_records_book.py --league <key> --prod` after the weekly build until MLB-283 wires it in.
- **The incremental watermark predicate changed.** Row selection is identical for any league with fewer than 100 periods per season, which is every league this project has seen; the change only matters at the short end of matchup length.

The guided Windows journey is unchanged: `START_ALMANAC.cmd`, `ROTATE_ESPN_CREDENTIALS.cmd`, `tools/windows_launcher.py`, `tools/setup_league.py` and `tools/build_release_bundle.py` are byte-identical to v2.0.1 (`git diff v2.0.1..v2.1.0 -- <those paths>` is empty), and the two credential modules changed only in docstring and error-message wording. The v2.0 clean-machine rehearsal therefore stands for this release and was not repeated.

The suite at this cut is **1883 pure tests** and **770 dbt data tests** over **103 models**, collected on a fresh clone.

Full detail in [CHANGELOG.md](CHANGELOG.md).
