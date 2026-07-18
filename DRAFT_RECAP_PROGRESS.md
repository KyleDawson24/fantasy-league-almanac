# DRAFT RECAP PROGRESS (overnight session, 2026-07-18)

Sprint doc for the CBS Draft Recap (HANDOFF_CBS_DRAFT_RECAP.md is the
map; this is what actually happened). Appended per round, walkback
style. **Museum rule held throughout: every CBS touch was a cookie-authed
GET; nothing was written to the league by any method.**

State at end of night: the **Draft Recap tab is live on the CBS dev
sheet** (three sections: 2026 recap, all-time board, draft classes),
fed by 4,269 parsed picks across 13 drafts with 99.98% identity/value
resolution. Open questions for Kyle at the bottom.

---

## Round 1 -- discovery: the catalog was on the page all along

The trial-and-error URL guessing Kyle anticipated wasn't needed: every
draft-results page embeds the full draft list in its sort `<select>`,
one option per draft with the EXACT server URL. Catalog (21 entries,
union across the 10 archived pages):

    /draft/results/{year}:{period}[:{title}]/
    period = 'Pre-season' | supplemental draft number ('2')
    pre-2017 keys are two-part ('2015:Pre-season'); titles vary freely
    ('BSB DRAFT-A-GANZA', 'Mini Draft', 'MEGA-DRAFT', '2', ...)

Draft records exist for **2009-2013, 2015, 2017-2026** (no 2014/2016,
nothing before 2009). The old fixed-key sweep (`{year}:Pre-season:
Pre-season`) had only ever hit drafts literally NAMED Pre-season; the
2022/2024 "captures" were happy empty shells of configured-but-never-
held pre-season drafts.

## Round 2 -- sweep (read-only GETs, all landed + content-verified)

`extract/cbs_ui_capture.py --drafts-sweep` (new mode): 21 drafts x
{round, team} views = 42 GETs, polite pacing, masthead-verified,
append-only landing at `data/cbs_raw/bsb/history/ui/drafts/keyed/`,
shapes recorded in the manifest + `verification_drafts_*.json`. The
original 10 bare captures stay untouched.

Key shape discovery: the views are server-side sorts honored ONLY where
the data exists. `/round` renders real round sections just for drafts
with recorded order; everywhere else it emits a broken skeleton (one
empty row per team). The TEAM view is the real record for the offline
era -- per-team lists, sometimes with a `Rnd/Pk` column ('1/3', '1/'
round-only, '/' nothing). **Total Fpts / Active Fpts columns ride the
2022+ pages** -- CBS's own draft-season value in both lenses.

## Round 3 -- parse

`extract/cbs_draft_parse.py` (new): keyed pages -> one NDJSON row per
table row (8,6xx rows) at `.../drafts/parsed/draft_rows.ndjson`, with
era-tolerant column templating (label rows re-template mid-table),
first-anchor player extraction (news-icon sublinks ignored), Rnd/Pk
splitting, and 'TEAM' totals-row furniture filtered. Census per page in
`parse_summary.json`.

## Round 4 -- stitching analysis (the verdicts)

| Year | Best evidence | Picks | Order verdict |
|------|--------------|-------|---------------|
| 2009 | round view | 512 slots | order recorded, **zero players anywhere** -- nothing to recap |
| 2010, 2012 | -- | 0 | pages render empty |
| 2011 | team view | 118 | plain-text names, no ids, no order; 15 teams |
| 2013 | team view | 462 | players only, no order |
| 2015 | team view | 446 | players only, no order |
| 2017 | team view | 96 | top-up draft, no order |
| 2018 | team view | 439 | players only, no order |
| 2019 | team view | 96 | top-up draft, no order |
| 2020 | 'BSB 2020 Draft' round view | 240 | 27 "rounds" are entry batches (same-team runs); **'2020 - 2' is the SAME 240 players recorded twice** -- deduped, not stitched |
| 2021 | Pre-season team view | 433 | no order; **'2021 - 2' is the same 433 players twice** -- deduped |
| 2022 | MEGA-DRAFT team view | 432 | no order; + page Fpts |
| 2023 | team view | 160 | top-up draft, no order; + page Fpts |
| 2024 | A-GANZA skeleton x '2024 - 2' lists | 419 | **split-brain**: order skeleton has zero players, player lists have zero order; the (team, k)-zip is NOT draft order (see round 6) |
| 2025 | Mini + Mega round views | 448 | TRUE order; Mini R1-12 then Mega renumbered R13-28 |
| 2026 | Mini + Mega round views | 480 | TRUE order; Mini R1-8 then Mega R9-30 |

Continuation rule findings: 2025/2026 mini->mega are genuine two-part
drafts (0 player overlap) and stitch per Kyle's rule. 2020/2021 pairs
are DUPLICATES (240/240, 433/433 overlap) -- treating them as
continuations would have double-counted entire drafts.

**The k-th-listed-player heuristic is dead.** Team-view list order ==
true round 100% for the online drafts (2025/2026 -- CBS sorts by pick
where order exists) but 9/240 for the offline-entered 2020, and
list-order-vs-value Spearman rho sits between -0.35 and +0.26 for every
no-order year (a draft-ordered list would trend strongly positive).
Offline lists ride roster order. No rounds were invented for those
years.

## Round 5 -- identity + value join

`output/cbs_draft_recap_data.py` (new provider): explicit per-year
assembly plan (`DRAFT_SOURCES`), warehouse joins via the record book's
calculated lens (`int_cbs__player_season_stats` CALCULATED_* by
cbs_player_id -- the mart-promotion question is flagged below) +
`dim_player_identity` name fallback (cbs_name_key ported: periods drop
with NO space -- the B.J. Upton lesson).

Two-way seam: value resolves through the mlbam spine. A '(Batter)'/
'(Pitcher)' pick keeps its half; a unified name sums every CBS id its
mlbam maps to that season, preferring discipline-scoped (split) ids so
a unified row can never double-count. Ohtani's unified-drafted years
now credit both halves (2021: 1102, 2022: 1249, 2023: 1117 -- CBS's own
page only credited one half, 633, in 2023; ours is RIGHT).

Resolution: **4,269 picks, 1 miss** -- 2011 'Chris Young' (Hot-Dog
Junkies), a genuine two-active-players homonym, flagged ambiguous and
never guessed. Zero-fills (identified player, no MLB production that
season) are genuine zeros per the ESPN mart's COALESCE-0 precedent.

Cross-check vs CBS's own page Fpts (1,765 comparable picks): **median
delta 0.0**, 73.8% within +/-2. The tails are all explained classes:
  * 2022/2023 pitchers: calc runs ~15% above page (Cole 778 vs 672,
    Webb 670 vs 564...) -- smells like the known platform-scoring
    wrongness (week-12 WALK class); calculated stays the lens.
  * 2026 rookies missing from the crosswalk (swept 2026-07-09, they
    debuted after): Justin Crawford (page 172 -> calc 0), Hao Yu Lee
    (96 -> 0), + 2022 Kelvin Gutierrez (15 -> 0). Refresh backlogged.

## Round 6 -- the 2024 zip is disproven

First render put **'Shohei Ohtani -2024' as the Top Pick of all-time
Round 19**. The A-GANZA skeleton's (round, slot, team) structure is
real, but filling team T's k-th slot with T's k-th listed player
inherits the roster-order flaw from round 4. 2024 is demoted to the
no-order tier ('not recoverable' on the tab); the zip code path stays
for inspection.

## Round 7 -- the tab (live on the dev sheet)

`build_draft_recap_rows` in `output/cbs_almanac_sheets.py` (+ DRAFT_TAB
registered in `build_all_tabs` after Advanced Standings, `_DRAFT_WIDTHS`,
dashboard freeze branch, Home-nav placeholder wired to a live link).
Three sections, house visual system (powder bands, italic size-9 notes,
pale-blue subtitle, `--` in note text):

1. **Draft Recap: 2026** -- ESPN's shape: Best Value Picks / Biggest
   Busts side by side (value = overall pick minus season-points rank,
   the ESPN metric), then the round x team board (Rd | Min | Median |
   Max | 16 franchise-abbrev columns), Mini+Mega stitched, bref links.
   Parity gap vs ESPN: no per-cell red->green paint yet (ESPN computes
   it writer-side; the CBS writer takes builder-side specs -- ~480
   repeatCell specs, deferred pending Kyle's call).
2. **All-Time Draft Board -- 16-Team Shape** -- team-agnostic re-cut
   per Kyle's spec (all-time Round N = overall picks 16(N-1)+1..16N);
   per-slot averages, per-round Med / Max / Top Pick ('Player -year'
   links), red->white->green gradient across the grid. Coverage is
   honestly ONE season right now (2025): 2026 is mid-flight (excluded
   from averages), 2024 fell in round 6, everything older has no
   recorded order. The band note owns this out loud.
3. **Draft Classes** -- the order-free digest every draft gets: Year |
   Picks | Rounds | Sequence provenance | best three picks (linked,
   with points) | Notes (dedupe stories, unresolved counts). This is
   where the 2011-2023 value story lives (Verlander 1010 in 2011,
   Ohtani's 1102/1249/1117 run...).

Render: targeted `cbs._write_tab` to the DEV sheet (resolved via the
registry sink; prod untouched), **write-twice idempotency proven**
(same gid, clean rerun), content verified by read-back. The dev Home
tab still shows the old placeholder text until the next full
`generate_almanac_sheet.py --league cbs-bsb` run (the nav link needs
the two-pass gid patch).

Tests: `tests/test_cbs_draft_recap_tab.py` (5 layout tests over
injected history, per the test_cbs_standings_tab template). No-warehouse
suite: **234 passed** (was 229). Warehouse-marked suite untouched.

---

## Round 8 -- the pre-2013 hunt (Kyle's late-night addendum)

Kyle's recollection ("no draft data until 2013 or 2015") checks out as
**2013** -- the first full record with player ids (462 picks). What
exists before it, and where else the old data could hide, is now swept:

| Avenue | Verdict |
|--------|---------|
| Draft pages 2009-2012 | 2009 = order skeleton, player cells EMPTY server-side (no ids, no anchors, nothing in attributes; 'Unknown 14/11' team labels smell like the same lossy migration). 2010/2012 = shell pages that don't even render the draft form. 2011 = 118 plain-text names (already in Draft Classes). |
| Pre-2009 draft pages | Don't exist. The year-by-year dashboards prove it from CBS's own side: 2009+ pages carry a per-year `DRAFT RESULTS` button; the 2001-2008 pages have NO such link -- nothing to link to. |
| Transaction logs 2001-2013 (both filters, full archive on disk) | ZERO draft events. Every 'draft' token count is exactly the page-furniture constant (29/file). Confirms the handoff's "CBS never logged drafts" for the early era too. |
| Team-overview pages (34) | Furniture only (2026 mock-draft nav + the March 2026 draft banners). |
| Message Board Archive | Live surface, honors `print_rows=9999` + `start_row` -- but **retains only the current season** (200 rows, all Apr-Jul 2026; deeper offsets empty; sort param ignored). One human thread ('Monarch draft list', 5/17/26) is mid-season 2026 chatter. Old threads are gone. Index landed at `data/.../ui/messages/feed_all.html`. |
| API draft endpoints (`league/draft/results` / `draft/order` / `draft/config`, whitelisted read-only in cbs_capture.py) | **INCONCLUSIVE -- CBS_TOKEN has expired** (known-good `league/details` also 401s now; last good 2026-07-09). Re-probe after a token re-mint. Odds are low (the fantasy API layer has been current-season-only under every prior probe), but it's the one un-closed door on CBS. |
| archive.org | Not probed -- the league site is login-walled, so crawlers only ever saw the login page. Structurally empty. |

**Conclusion: CBS holds no recoverable draft data before 2011, and
nothing at all before 2009. If pre-2013 completeness matters, the data
has to come from off-platform artifacts** -- see new questions 12-14.
(Reminder: the degraded fallback exists regardless -- the walk-back's
opening-roster recovery can say WHO started each season 2001+, which is
a "draft class" in all but sequence.)

---

## Open questions for Kyle (morning review)

1. **Value lens.** Defaulted to calculated season points (the record
   book's lens, matching the almanac's platform-points ruling). The
   pages' own Total Fpts AND Active Fpts are captured as alternate
   lenses (2022+ only). Keep calculated? (Everything downstream reads
   one `value_lens` knob.)
2. **All-time board coverage.** With order existing only for 2025
   (complete) + 2026 (in progress), the board today is really "2025,
   re-cut". Options: (a) leave and let it accrue year by year, (b)
   include 2026-to-date with a caveat note, (c) hold the section until
   two complete ordered years exist. Currently (a) with the note.
3. **2020/2021 duplicate second entries** -- I deduped rather than
   stitched (player sets identical). Sound right against your memory
   of those seasons?
4. **2024 verdict** -- order not recoverable (skeleton has no players;
   lists have no order; the zip married them wrongly). Accept, or do
   you remember an offline artifact (spreadsheet/email thread) that
   recorded the real 2024 order? The pipeline takes a manual seed
   cleanly if one exists.
5. **Same question for 2009-2012** -- 2009 kept sequence but lost the
   players; 2010/2012 rendered nothing; 2011 is names-only. Any
   offline records worth seeding?
6. **2011 'Chris Young'** (Junk Drawer All-Stars) -- the pitcher (SDP) or
   the outfielder (ARI)? One answer resolves the last unvalued pick.
7. **Crosswalk refresh** for 2026 debuts (Crawford/Lee show 0; page
   says 172/96) -- green-light a targeted re-run of the MLB-70
   crosswalk sweep?
8. **2022-2023 pitcher scoring drift** (calc ~15% over page Fpts, only
   those years, only pitchers) -- leave as a documented platform
   wrongness, or dig into which rule (IRSTR/QS?) diverged?
9. **2026 board coloring** -- want the ESPN red->green per-cell paint
   on the round x team board? (Builder-side repeatCell specs; ~480
   requests per render; doable, just deliberate.)
10. **dbt-ification** -- tonight's provider reads the parsed NDJSON
    directly (loud STOPGAP note in the module docstring) and reads
    `int_cbs__player_season_stats` (an int, not a mart -- would be the
    second logged exception). The real seam per the handoff: land picks
    in RAW.CBS_DRAFT (cbs_load family) -> `stg_cbs__draft` -> union
    under the ESPN chain so `mart_draft_board` serves both leagues,
    with the draft-season points join inside the mart. Green-light for
    next session?
11. **Layout** -- one tab with three sections vs splitting all-time
    onto its own tab; also whether the current-year section should sit
    below the all-time section instead of above.
12. **CBS_TOKEN re-mint** (reCAPTCHA login, your manual step) -- needed
    to (a) finish the API draft-endpoint probe (the last un-closed CBS
    door for old drafts) and (b) unblock the API capture pipeline
    generally before season-end. Procedure documented in MLB-13's
    2026-07-07 "60-second unblock" comment (+ SETUP.md section 6):
    logged-in browser -> any league page (e.g. /standings/overall) ->
    View Page Source -> Ctrl+F `var token` -> copy the quoted ~128-char
    value -> `CBS_TOKEN=<value>` in the root .env. TTL datum MLB-13
    asked us to record: minted 2026-07-07, verified alive 2026-07-09,
    dead by 2026-07-18 -- observed lifetime 2-11 days, so plan sweeps
    accordingly. (`CBS_WEB_COOKIES` is separate and still authenticated
    as of tonight; grab a fresh cookie header in the same trip if you
    want both topped up.)
13. **Off-platform artifacts for 2001-2012 drafts**: CBS emails owners
    draft-results/recap messages -- worth searching your Gmail for
    circa-2001-2012 cbssports.com draft mail? Old commissioner
    spreadsheets / league-email threads count too. The pipeline takes a
    manual seed CSV cleanly (year, team, player, round/pick optional).
14. **Did 2001-2008 drafts even happen on CBS?** The site has no
    artifact at all for those years (round 8). If the early league
    drafted by email/in-person and hand-entered rosters, there was
    never anything to lose -- good to know before we mourn it. The
    opening-roster walk-back fallback covers those years either way,
    if you want degraded "draft class" rows for them.

## Ops notes

- Branch `claude/modest-montalcini-3af8c4`, committed locally, **not
  pushed** (overnight work; push after your eyeball).
- New/changed: `extract/cbs_ui_capture.py` (drafts-sweep mode),
  `extract/cbs_draft_parse.py`, `output/cbs_draft_recap_data.py`,
  `output/cbs_almanac_sheets.py` (builder + registration + Home nav
  line), `tests/test_cbs_draft_recap_tab.py`, this doc.
- Raw artifacts (outside the worktree, under the main root):
  `data/cbs_raw/bsb/history/ui/drafts/keyed/` (42 pages),
  `drafts/parsed/draft_rows.ndjson` + `parse_summary.json`,
  `verification_drafts_*.json`, manifest appends.
- QA harness: `python output/cbs_draft_recap_data.py` prints the
  assembly/resolution tables, the page-vs-calc cross-check, the
  list-order rho test, and the miss/two-way/crosswalk-gap samples.
- Dev-sheet rerender of just this tab: the scratch runner pattern --
  build rows, `cbs._write_tab(spreadsheet, cbs.DRAFT_TAB, rows,
  formats, value_input_option='USER_ENTERED')`.
