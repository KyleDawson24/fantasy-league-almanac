# Release Notes -- v1.9.0

29 commits since v1.8.0.

**The almanac now ends where it was always supposed to end: a workbook in your own Google Drive, that your league can open.** v1.8.0 got a stranger's clone as far as preview files on disk. This release carries that the rest of the way -- one command after configuration walks every season your league has, builds locally, creates a spreadsheet the app owns, renders the almanac into it, and hands you a link.

Two things had to be true first, and both are the bulk of this release. ESPN's matchup calendar had to come from ESPN rather than from a CSV a human maintained, because a stranger has no way to fill that file in. And the workbook path had to ask for the narrowest Google permission that could do the job, prove what it was granted, and never publish anything without being told to.

Along the way the almanac gained a surface: **the Rivalry Matrix**, every active team against every other, at the bottom of Advanced Standings in both books.

Minor rather than major. Everything here is additive, and no existing install has a migration step to run.

---

## The public path: a workbook, not a directory of files

After the `.env` fields and the registry entry are configured -- QUICKSTART walks both -- the supported path is one command:

```bash
python tools/create_public_almanac.py
```

It reads the season bounds already on your registry entry, runs one extraction invocation for each applicable season -- closed-period box scores, settings, standings, transactions, matchup membership and calendar -- loads the local parquet, builds DuckDB, and only then creates the workbook. A season that fails or cannot be derived stops the run *before* a workbook is generated, rather than producing a book with a hole in it.

**It asks Google for `drive.file` and nothing else.** That scope grants access only to files this application itself created. It cannot list, read or search anything else in your Drive, and that is a property of the grant rather than a promise about our code. The maintainer's own broader profile still exists for the standing dev/prod books, but the two profiles have different scopes, different client configuration, different token caches, and are asserted to be unable to reach each other.

**The workbook starts private and stays private until you say otherwise.** It is created, the almanac is rendered into it, and only then -- immediately before anything changes -- does the app name the league and member information the book contains and require you to type `YES`. Automation has to pass `--confirm-link-sharing` to make the same affirmation deliberately. Only after that does it set anyone-with-the-link viewer, read that permission back from Drive to confirm exactly one `anyone` reader grant with discovery off, and print `Your almanac: <link> -- share-ready.` Any step failing withholds that line and prints the workbook URL with a plain description of what went wrong. Your workbook is never deleted to tidy up after a failure -- it is in your Drive and it is yours.

**Local DuckDB is the default and Snowflake is an explicit choice.** The public command forces the local parquet/DuckDB pipeline; a warehouse is reached only if you deliberately add `--advanced-snowflake`. Nothing leaves your machine except the almanac you asked to be written.

**The application carries its own Google identity, and that identity is not in git.** Running the workbook path used to mean creating a Google Cloud project, enabling APIs, configuring a consent screen and downloading an OAuth client -- five console steps before the first spreadsheet. The published tool now brings its own. The credential is injected into the release archive by `tools/build_release_bundle.py` from a git ref, never committed: a public repository is not a distributed artifact, GitHub's partner scanning reads public history independently of anything a repository can configure, and history cannot be edited after the fact. The tracked descriptor is empty, which is also the fail-closed state a clone should be in. The builder refuses to run if the exported source already contains a credential-shaped literal, and a tracked-tree census asserts none is committed at all.

### Windows, and what that does and does not mean

**The public Google workflow is Windows-only in v1.9,** because the grant is stored in Windows Credential Locker and the release refuses to fall back to a plaintext token file. That refusal is the point: an OAuth refresh token sitting in a JSON file next to the code is protected by file permissions and nothing else. An older `output/.sheets_public_oauth_token.json` is migrated only after the secure write succeeds and is verified, and its plaintext copy is then removed.

**This does not make the rest of the application Windows-only.** The local path -- extract, DuckDB, dbt, and the preview files and BBCode the project has always produced -- has no such dependency. It is the Google workbook flow specifically that is gated, and lifting that gate is a matter of adding the other platforms' secure stores.

## Weeks and dates now come from measurement

An ESPN user no longer fills in `matchup_schedule.csv` before anything works. The quickstart has one fewer manual step -- removed, not reworded.

The old dependency was genuinely circular: the extract read the CSV to learn which dates belonged to which week, turned those dates into scoring periods, and then stamped that answer onto every row it wrote. The matchup period in the warehouse originated in a file a human maintained, so reading it back proved nothing about the platform.

**ESPN already publishes the answer.** A box-score run captures the season's matchup document once and reads the weeks, and the days inside each week, out of that same object -- ESPN's own numbering, not ours. For the calendar, ESPN serves scoring periods as *days* but no dates, so the dates come from MLB's own published season start, captured from the free public MLB Stats API into a narrow snapshot of its own rather than grafted onto ESPN's payload.

**It fails closed, everywhere it can.** A period still being played is not a settled result and does not count. A document the parser cannot read stops the run rather than producing a confident half-answer -- and keeps its snapshot as evidence, because ESPN does not re-serve what it has moved on from. A season with zero closed weeks is reported honestly rather than having a week invented so the models come out non-empty. A history backfill plans the whole valid range before any ordinary season write begins, and its refusals are deliberately not uniform: a structurally invalid ESPN document or a failed MLB calendar opens no sink and writes nothing at all, while a season whose membership cannot be derived preserves that one season's diagnostic snapshot -- and nothing else -- before exiting non-zero. Where the league's normal week length cannot be established, the answer is recorded as *unknown* rather than as *normal* -- those are different facts, and the difference now survives all the way to the surface.

**Season-long points is the measured exception to “unfinished means excluded,” not a weakening of it.** The first untouched-machine rehearsal reached a real ESPN type-5 league in its active first season: ESPN reports one season-long, multi-team container and `latestScoringPeriod` as the current daily endpoint. The extractor reads day-specific fantasy rosters for days 1 through that endpoint and feeds the ordinary player, team and season facts under reporting period 1. It does not invent opponents, weekly W-L results or a closed H2H matchup. The unfinished current matchup in an ordinary H2H league remains excluded; rendering that live week is a separate enhancement.

**The same rehearsal closed five stranger-path failures that synthetic data did not expose.** DuckDB's ordinary lateral JSON-array flatten retained each parent document once per free agent and exhausted the public 6 GB cap; the free-agent expansion now uses the already-proven streaming shape and the measured 142-day payload completes under that cap. ESPN also authorized every ordinary league read while returning 401 for that member's separate communications feed. That feed now fails unavailable rather than empty: no incomplete transaction snapshot is written, dependent blocks are omitted or labelled unavailable, and the rest of the almanac continues. ESPN then reset one connection after 114 successful scoring-day reads; the two day-grain sources now retry transport failures, timeouts, throttling and server errors three times with short backoff, while bad credentials still fail immediately and exhausted retries still refuse the entire partial day. The workbook render exposed the final two: DuckDB season keys reached Google's JSON client as Python `Decimal` objects on the all-time draft board, and a present-but-empty standings block emitted a conditional-format rule with no range. Season identifiers are now normalized at the display seam, and empty ranges emit no rule instead of cancelling the tab's formatting pass.

Two consequences worth calling out:

- **Matchup periods are scoped to a league.** The old calendar had no league column, so one league's weeks reached any other league sharing those period numbers. The dimension is keyed on league now, and a league inherits no calendar, flags, overrides or playoff labels from its neighbours.
- **Records are gated on known shape.** A week whose shape nobody can prove is no longer eligible to produce a record holder. The Python side had been failing open here -- an unknown period read as an ordinary one and could be marked a record -- while the SQL side dropped it by accident of three-valued logic. Both now read the same explicit gate.

## The Rivalry Matrix

Every active team against every other, at the bottom of Advanced Standings in **both** books. A standings tells you who is ahead; this tells you against whom.

There is one matrix, and what counts as a game follows your league's format -- **read from the data, never from which site the league is on.** A head-to-head league sees completed matchups. A points league, which has no matchups to have a record about, sees each completed season scored as one game, won by whoever put up more points over the whole year. A CBS head-to-head league and an ESPN points league both exist, so a platform-name check would misfile both.

It aggregates by **team**, not by platform id. A franchise whose id was re-minted, or that was renamed, brings its whole history with it, and a league can declare two ids to be one team by giving them the same canonical name in its lineage configuration. Two teams that merely happen to share an *observed* name stay separate -- observation is a coincidence, configuration is a statement -- and if two live teams share a configured name the build warns so an accidental collision can be corrected.

**Only finished results count.** A week still being played is not a result, and neither is a whole season nobody can prove is over. Seasons the platform has published final standings for count without needing a schedule capture at all. Only seasons both teams were actually in the league for are compared.

**Unknown and proven-zero do not look the same.** A team has no record against itself, so the diagonal is blank. Two teams that have genuinely never met read 0-0, which is a fact about the league. But a league whose history nobody can prove yet gets *no grid at all* -- drawing a full square of 0-0 would claim these teams have played and nobody won, which is a different and false statement. It says so plainly instead, and names what to capture.

The block sits indented at column C to match the section above it, and cells are shaded red-to-white-to-green by winning percentage on the house gradient, centred on .500. The text is untouched -- a W-L is what people quote at each other, and the colour is a second channel on it. A blank diagonal and a never-met 0-0 get no colour, because shading 0-0 deep red would invent a drubbing out of two teams that have never played.

## Underneath

- **`dim_matchup_period` builds on Snowflake again.** It carried `is_abnormal is not true`, and Snowflake has no `IS [NOT] TRUE` predicate -- it rejects the spelling outright, so the model and the 13 relations downstream of it failed to build. It reached `main` because the contract suite builds that model against DuckDB, where the spelling is ordinary SQL -- the one engine that cannot catch it. The guard is therefore a source-text scan rather than another build, since a build-based check would share the blind spot.
- **A clone stopped depending on the maintainer's machine.** Three ways: a test file that drove the real CLI was silently authenticating with real cookies loaded from a local `.env`; four dbt fixtures assumed installed packages a fresh checkout does not have; and the matchup-period contract was being verified against whatever calendar the checkout happened to carry. Split apart, the public contract now builds against a synthetic seed the test generates, and the claim only the real league can make is checked privately and skips loudly when it cannot run.
- **The all-blank installation actually works.** The quickstart says every league-config file may stay blank; on that exact state the build died, because an empty CSV has no column types to infer and `league_key` arrived as an integer that met a string. All fourteen committed templates declare their types now, and the test builds the fresh-clone state from what is actually committed rather than from any working copy.
- **The PII guard sees whole identity families.** It now derives its inventory per category from whatever source is authoritative -- owners, franchise names and labels, division names, team ids, league ids -- so adding a league covers its identities with no list to maintain. Two rules changed to make that safe: a team label is whatever the league says it is (an emoji, an all-digits name), and numeric identifiers are entropy-gated rather than grepped, since one- and two-digit team ids are also every week number in the tree. Class-wide amnesty is gone: every reviewable hit carries its own recorded disposition keyed to the *occurrence*, an unreviewed one fails by default, and the pre-push hook is tracked rather than living in one uncommitted file on one machine.

## What was measured

- **Goldens moved for exactly two files, and for exactly the expected reason.** ESPN and CBS Advanced Standings both gain the matrix block and then shift it two columns right. Across both corpora **38 of the 40 private golden TSVs are unchanged byte-for-byte**, and no file was added or removed. Both drifted files hold their row count exactly (ESPN 172, CBS 203); every differing line is the same text two columns over, with its non-empty cells equal in the same order at index + 2; ESPN lines 1-153 and CBS lines 1-184 compare equal. Cell shading is formatting and never reaches a TSV baseline at all, so its cover is the renderer tests rather than the goldens.
- **The warehouse golden suite passed 27 of 27** -- both byte-diffs plus both BBCode baselines, which came back byte-identical.
- **The derived calendar reproduces the hand-maintained one.** Anchors of 2025-03-18 and 2026-03-25 match the retired seed across all 44 closed periods of both seasons -- long opening weeks and both 14-day All-Star weeks included. 2025 derives its full 1..26 with zero membership and zero abnormality mismatches; 2026 correctly stops at 18, because its last scoring day has not arrived. The eligibility gate produced 44 eligible rows: exactly the 44 that qualified before.
- **The PII guard passes non-degraded**, with the private map in place: 398 of the 414 files tracked at HEAD swept for 322 strings, 348 occurrences allowed by category rule and 278 carrying a recorded disposition, none unreviewed.
- **Two implementations, one specification.** The matchup parser exists in Python and in SQL, and they are held against each other over synthetic seasons covering every status rather than each against its author's intent.
- **Project inventory at this cut:** 95 dbt models, 20 seeds, 717 data tests, 29 sources, 4 exposures; 1512 pure pytest tests and 27 warehouse-marked, counted on tracked files only.

---

## Limits

**CBS is not part of the supported stranger journey.** The public path is ESPN, and ESPN is the hard requirement. The Rivalry Matrix renders on the CBS book and the marts are platform-general, but CBS capture still needs the browser-credential route and is not something a stranger can run. It is an urgent fast-follow, not a v1.9 gate.

**This is not yet complete onboarding.** The one command is *post-configuration*: it assumes the `.env` fields and the registry entry are already filled in correctly. The guided fields-file onboarding is MLB-31/MLB-207 and has not shipped. There is no zero-input installer and no double-clickable executable, and this release does not claim one.

**The untouched-machine rehearsal found the blockers; the isolated re-cut completed the application path.** A clean Windows machine and a league this project had never seen established the install and live-ESPN path, then exposed the season-long-points gap fixed in this cut. Limited access to that machine moved the final iterations to a brand-new extraction of the tagged ZIP on the maintainer machine, reusing its installed interpreter but no project data: the re-cut completed all 142 reportable scoring days, all 836 dbt nodes, the Google workbook render and verified link sharing. Repeating the final ZIP on the untouched device remains the last confidence rehearsal before external handoff, not an implementation step still missing from the bundle.

**Google branding review is still pending.** The application's publishing status is **In Production**, and its homepage, Privacy Policy and Terms are live at kpdawson.com. Branding verification has been submitted; Google's review of it has not come back. Until it is approved, Google may withhold the configured branding and consumers may see an unverified-app warning, so the consent screen a stranger meets can differ from the one described here. That gate is external and outside this project's control, and it may change before publication.

**The public Google flow is Windows-only,** as above. The local, non-Google application is not.

**A league in its first season still sees all-time surfaces duplicating the current one.** They build and render rather than crashing, but with one season of history "all-time" and "this season" are the same numbers twice. Unchanged from v1.8.0.

## Deferred on purpose

- **No activity rule was invented for the matrix.** The project has no authoritative definition of "still active"; the choices are written down rather than guessed at, and the axes use the live-identity resolution that already existed.
- **No confidence weighting on the shading.** A 1-0 shades full green. Adding shrinkage for small samples would be inventing a rule nobody asked for.
- **Season points are compared on raw platform totals** -- no margin weighting, no normalising for periods played, and a season one side sat out counts for nobody.
- **The matchup-period typing boundary that predates this work is untouched.** It is documented, and it is not what any completion evidence rides on.

## What 2.0 is for

Unchanged, and the second keystone is now standing:

> **A stranger with an ESPN or CBS league enters some credentials, runs some things,
> and gets an almanac their league can open.**

MLB-208 -- extract writes RAW locally -- closed in v1.8.0. **MLB-209 -- the journey ends in a shareable workbook -- is what this release closes**, for ESPN, after configuration. What stands between here and 2.0 is the onboarding that fills in the configuration (MLB-31/MLB-207), the rehearsal on a machine and a league nobody has touched, and CBS.

Version numbers are promises here. 2.0 ships when that journey is real for a stranger on either platform, which is exactly why this cut is 1.9.0.
