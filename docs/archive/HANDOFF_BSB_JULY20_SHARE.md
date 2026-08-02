# HANDOFF — Ship the July 20, 2026 BSB Almanac to the father-in-law

**One singular goal:** get the July 20, 2026 updated version of the Box
Score Baseball (BSB) Almanac ready to go and share it with Kyle's
father-in-law (the league's commissioner-emeritus and the reason the
museum rule exists). Everything in this doc serves that goal; anything
else waits.

**Four take-homes** (Kyle's list, verbatim intent — NOT necessarily the
work order):
1. The updated Almanac (data through ~July 20).
2. A simple email explaining what it is — **4 sentences TOPS** of
   overview, then **1–2 sentences per tab type**.
3. Google Sheet(s) he can fill himself to supply the "lore"/history
   we're missing — **≤3 sentences of instruction per sheet**.
4. A first-pass "User Guide" — spares gory details; just what a person
   needs to read everything, plus a couple FA(nticipated)Qs.

**Hard rule for this goal:** the email, the lore workbook, and any share
settings are OUTWARD-FACING to a real person. Draft everything; **Kyle
reviews and Kyle sends/shares.** Never email the father-in-law or flip
Google sharing yourself. And the CBS league itself stays read-only
forever (museum rule) — this whole deliverable is GETs + our own sheets.

---

## 0. Environment (same as the draft-recap sessions)

- Worktree: `.claude/worktrees/modest-montalcini-3af8c4` (branch
  `claude/modest-montalcini-3af8c4`), run with the worktree as cwd.
- Interpreter: repo-root `.venv`
  (`C:\Users\kyled\projects\espn-league-manager\.venv\Scripts\python.exe`);
  `.env` resolves upward. `data/` lives in the MAIN checkout.
- The branch is **~20 commits ahead of origin, unpushed** (origin is the
  PUBLIC portfolio repo — push deliberately; `dev` remote is the safe
  backup). If this runs in cowork, the branch must be pushed somewhere
  first.
- League: `cbs-bsb`. Renders default to the DEV sheet
  (`CBS_SHEETS_DEV_ID`); the father-in-law-facing sheet is PROD
  (`CBS_SHEETS_OUTPUT_ID`, created + shared under MLB-49). `--prod` is
  always a deliberate, typed choice.
- Suites: `pytest tests/ -q` = 234 no-warehouse; keep the
  warehouse-marked suite green. ESPN goldens must not move for CBS-only
  work.
- Prior context if needed: `HANDOFF_DRAFT_RECAP_SESSION.md` (the freshest
  full-system map), `DRAFT_RECAP_PROGRESS.md` (rounds 1–12),
  `project_cbs_league` memory (API decoys, scoring history, auth).

---

## 1. Take-home #1 — the updated Almanac

The tab set is DONE (Home, Records, Advanced Standings, Draft Recap, 16
team pages — all rendered current on dev as of 2026-07-18). "Updated"
means fresh DATA through ~July 20, then re-render.

**Refresh sequence (run on/after July 20 so the data is through the
19th–20th):**
1. **Auth check first.** `CBS_TOKEN` was re-minted 2026-07-18; observed
   TTL is 2–11 days, so it MAY still be alive — verify by content
   (`python extract/cbs_capture.py --probe`), never assume. If dead:
   re-mint per SETUP.md §6 (the `access_token` inside the page's global
   player-search function — NOT the look-alike CBSi token; network-tab
   fallback documented there). `CBS_WEB_COOKIES` isn't needed for a data
   refresh (no UI re-scrape required), but topping it up in the same
   trip is house practice.
2. **CBS fantasy layer:** `python extract/cbs_capture.py --capture`
   (idempotent; extends 2026 rosters + period standings to date).
3. **Universal MLB stats:** refresh 2026 gamelogs via the
   `extract/mlb_stats.py` / `mlb_load.py` family (check each script's
   docstring/`-h` for the incremental invocation — don't guess flags).
   This is what moves every point total on the almanac.
4. **Optional but nice for the Draft Recap:** re-run the crosswalk sweep
   (`extract/mlb_crosswalk.py`) so 2026 debuts map — Justin Crawford
   (~172 pts) and Hao Yu Lee (~96) currently show 0 on the draft board
   because they debuted after the 2026-07-09 sweep. Known cosmetic gap;
   fixable here, or leave and note it.
5. **Warehouse:** `dbt build` from `dbt_league/` (remember the half-land
   gotcha: raw present but marts stale = the incremental chain didn't
   rebuild; `dbt build` fixes it). Then `pytest tests/ -q`.
6. **Render DEV:** `python output/generate_almanac_sheet.py --league
   cbs-bsb`. **Kyle eyeballs the dev sheet — that's the gate.**
7. **Push PROD (the father-in-law's copy):** same command with `--prod`,
   only after the eyeball. Confirm with Kyle immediately before — this
   is the outward-facing publish.
8. Sanity read-back a couple of cells post-render (house law: verify by
   content). Rerun-idempotency is already proven for these writers.

**Notes:** the pace-adjusted numbers (Draft Recap all-time board) and
"period N of 27" style labels update themselves from the data. The
2026-07-18 draft API snapshot under `data/cbs_raw/bsb/2026/draft/` does
NOT need re-pulling for this (the Mega draft is completed/static).

---

## 2. Take-home #2 — the email (draft for Kyle to send)

Constraints: **≤4 sentences of overview**, then **1–2 sentences per tab
type**. Write it in Kyle's voice to his father-in-law: warm, zero
jargon, no code words ("walk-back", "marts", "lenses" are all banned).
Deliver as text Kyle can paste into Gmail. Include (a) the almanac link
placeholder for the PROD sheet, (b) one line inviting him to the lore
workbook (take-home #3), (c) optionally one line pointing at the User
Guide (take-home #4).

Raw material — the five tab types in plain English (compress to 1–2
sentences each; verify against the live sheet, don't trust this doc
blindly):
- **Home** — the front page: links to everything, a glossary of the
  point terms, and "All-League" best-lineup boards for this season and
  all-time; also notes where each era's stats come from.
- **Records** — the record book: best single seasons and best careers in
  league history, by stat, including the fun negative ones.
- **Advanced Standings** — this year's race period by period (with a
  chart), plus every season's final standings back to 2001 with
  champions marked.
- **Draft Recap** — this year's draft: every pick, the steals and the
  busts; below it, an all-time view of what each draft slot has
  historically returned, and a year-by-year "draft classes" section.
  (CBS only kept real draft order from 2025 on — older years show WHO
  was drafted but not the order; that's exactly what the lore workbook
  can fix.)
- **Team pages (one per franchise)** — each team's best-ever lineup
  (this season and all-time), the bench, and the best individual
  seasons by position for that franchise.

---

## 3. Take-home #3 — the lore workbook (he fills it, we ingest it)

**What it is:** ONE new Google Sheet workbook ("BSB League Lore" or
similar — Kyle names it), tabs below, structured so his answers land
machine-readable and flow straight into existing seed shapes. Build it
with the sheets client (gspread, same auth as the almanac writers) as a
NEW spreadsheet — do NOT add tabs to the almanac itself. Kyle shares it.
**≤3 sentences of instruction per sheet**, frozen in row 1–2, then
pre-filled skeleton rows wherever we already know the frame (years,
teams) so he only fills blanks.

Suggested tabs (each maps to a known gap from the draft-recap dig):
1. **Old Draft Results (2001–2024)** — columns: `year, round, pick_in_
   round (optional), team, player, notes`. Pre-fill year blocks. This
   feeds the Draft Override seed (MLB-91 shape) and widens the Draft
   Recap "Coverage" line automatically once ingested. Priority years:
   2001–2010 + 2012 (nothing recorded anywhere), 2024 (we have the
   pick-order skeleton but not who went where), 2009 (we have order,
   CBS lost the players).
2. **Champions & Asterisks** — columns: `year, champion (pre-filled
   from final standings), correct? (Y/N), actual champion if different,
   story/notes`. Champions were never formally named on CBS; we derive
   from standings — this confirms or corrects.
3. **Owners & Franchises** — columns: `team name (pre-filled per era,
   incl. the "Unknown 11"/"Unknown 14" ghosts from 2009), years, owner,
   notes`. Extends the MLB-64 franchise-continuity harvest.
4. **Quick Answers** — a two-column Q/A sheet of specific one-cell
   questions we already have queued, e.g.: 2011 draft "Chris Young" —
   the pitcher or the outfielder?; did 2001–2008 draft on CBS at all or
   offline?; does anyone have old CBS draft-results emails?; is the
   2020 double-recorded draft one event or two?
5. **League Lore (free text)** — origin story, division names (Bob
   Uecker / Branch Rickey), rivalries, anything. No structure demanded;
   this one's for flavor and future recap copy.

**Ingestion note for later (not this session's goal):** tabs 1–3 map to
seed CSVs (draft override per MLB-91; champions/owners extend the
MLB-64 sheet→harvest→seed pipeline, which already exists and is the
precedent to follow).

---

## 4. Take-home #4 — the first-pass User Guide

**Scope discipline:** this is the SHORT version. The full guide is the
MLB-74 family (75 navigation / 76 points lenses / 77 stat sources / 78
eligibility & two-way / 79 publish+wire) — use those tickets as the
outline but write maybe a page and a half total. A reader-facing Google
Doc (simplest; MLB-79's "decide the home" can stay open), linked from
the email and later from the almanac's Home tab.

Contents: one short "how to read this" paragraph per tab type (reuse
take-home #2's material, slightly expanded), then the FAQs. Anticipated
Qs with the honest short answers (long versions live in
DRAFT_RECAP_PROGRESS.md and the CBS memory — spare the gore):
- **"These point totals don't match what CBS showed that year."**
  Right — we re-score every season under the CURRENT scoring rules so
  eras compare fairly. Concretely: the league added Quality Starts (+4)
  and Inherited-Runners-Stranded (+2) in 2024, so pre-2024 pitchers show
  more points here than CBS awarded at the time.
- **"Why does draft history only go back to 2025?"** CBS only recorded
  true pick ORDER for the 2025–26 online drafts; 2011–2023 kept the
  players but not the order, and nothing survives before 2011. (Cue the
  lore workbook.)
- **"Why is Ohtani listed twice?"** CBS splits two-way players into a
  Batter and a Pitcher asset; we keep the halves separate where CBS
  does, and combine them where it's about the person.
- **"What does 'paced' mean on the all-time draft board?"** Part-seasons
  (like the current one) are scaled to a standard season length so they
  can sit next to full ones; single-pick "Top Pick" values are never
  scaled.
- **"How accurate is the old stuff?"** Stats are real per-game data from
  2007 on; earlier years are season-level. Who-had-whom is exact for
  2001–03 and 2021+, and carefully estimated for 2004–20 — the Home tab's
  stat-sources table says which is which.

---

## 4.5 STATUS UPDATE — the §1 data refresh ran 2026-07-19 late-night

Take-home #1 is substantially DONE for the dev sheets: CBS capture PASS
(118 roster days, 17/17 periods) + loaded; ESPN MP15 + season
transactions extracted (the Sunday slate was complete at extract time —
15 games / 440 players in the final scoring period); MLB 2026 gamelogs
fully re-fetched (1,262 files) PLUS the new `mlb_stats.py --discover
2026` pass (151 pool players we'd never fetched, Crawford/Lee included,
now on disk); warehouse reloaded (MLB_GAMELOGS 1.80M rows / 3,995
players); `dbt build` fully green (548 PASS / 0 ERROR); BOTH dev sheets
rendered (ESPN summary confirms "2026 MP15"; CBS all 20 tabs).

Remaining for the July-20 share: Kyle's dev eyeball → `--prod` push
(§1 steps 7-8), and optionally the CBS-side crosswalk refresh (the
MLB-side stats for the 9 prospect picks are now on disk, but the CBS
draft tab still shows them 0 until the CBS↔MLBAM crosswalk maps them).
Weekly-refresh recipe going forward: delete current-season gamelog
files + `mlb_stats.py --min-season <yr>` (no --force) + `--discover
<yr>` — never the full-sweep --force again.

Incident log (relevant if numbers look odd): a main-checkout extract at
20:24 landed 14 BOX_SCORES rows with NULL league_key (main lacks the
MLB-57 stamping, which lives on this branch); repaired by stamping
'espn-main' per the MLB-57 backfill rationale, rebuilt green. Until the
branch merges, extracts should run FROM THE WORKTREE.

## 5. Suggested working order (differs from the take-home order)

1. Lore workbook + email draft + user guide draft FIRST (no data
   dependency — can be built any day and reviewed by Kyle async).
2. On/after July 20: the data refresh + dev render (§1 steps 1–6).
3. Kyle's eyeball → prod render (§1 step 7).
4. Kyle sends the email with the links.

## 6. Open questions for Kyle (defaults in parentheses)

- Prod sheet share state: is the father-in-law already on the MLB-49
  share, or does the email carry a fresh share link? (Assume already
  shared; verify before sending.)
- Lore workbook: one workbook / five tabs (default) vs multiple sheets?
- User Guide home: standalone Google Doc (default) vs an almanac tab?
- Crosswalk re-sweep for Crawford/Lee before the share (default: yes,
  it's cheap and the draft tab looks better) — needs a live CBS_TOKEN.
- Does the July-20 render wait for any keeper/lore corrections, or ship
  as-is and iterate? (Default: ship as-is; the lore loop is the whole
  point of take-home #3.)

## 7. Conventions that bite here

- Museum rule: GETs only against CBS, verified by content, politely
  paced. No exceptions for "just one quick check."
- `--prod` only after Kyle's dev eyeball, confirmed in the moment.
- Kyle sends all outward communication; agent drafts only.
- Commits: first-person, no AI-attribution trailers; BRAINTHOUGHTS skim
  at any push; board changes mirror to `docs/roadmap/`.
- Sheets writes: one styling batch per tab, 70s quota backoff, prove
  rerun-idempotency on anything new (write-twice).
