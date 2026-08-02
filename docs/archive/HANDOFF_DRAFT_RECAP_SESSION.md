# HANDOFF — Draft Recap build + almanac format overhaul (2026-07-18)

Self-contained handoff for continuing in cowork. Covers the whole
session: building the CBS Draft Recap from scratch, mirroring it to
ESPN, the big format overhaul on both books, and the follow-up
investigations. The round-by-round detail lives in
`DRAFT_RECAP_PROGRESS.md` (rounds 1–12 + an open-questions section); the
INCOMING map that started it all is `HANDOFF_CBS_DRAFT_RECAP.md`. This
doc is the curated top layer.

---

## 0. State at handoff

- **Branch:** `claude/modest-montalcini-3af8c4` (a git worktree at
  `.claude/worktrees/modest-montalcini-3af8c4/`). **17 commits ahead of
  origin, NOT pushed** — origin is the PUBLIC portfolio repo, so pushing
  is a deliberate act (see BRAINTHOUGHTS skim rule). **To use in cowork
  you'll likely need to push the branch first** (or push to the `dev`
  backup remote). Tip commit: `8c46491`.
- **Tests:** 234 no-warehouse green (`pytest tests/ -q`); warehouse
  byte-diff green; all goldens re-anchored.
- **Dev sheets:** both Draft Recap tabs rendered current — CBS
  (`cbs-bsb`) and ESPN (`espn-main`). Kyle's eyeball of the dev sheet is
  the merge gate.
- **Interpreter/cwd:** run everything from the worktree with the
  repo-root `.venv`
  (`C:\Users\kyled\projects\espn-league-manager\.venv\Scripts\python.exe`).
  `.env` resolves upward from the worktree. **`data/` lives in the MAIN
  checkout**, not the worktree (raw archives are shared, gitignored).

---

## 1. What this session delivered

A **Draft Recap tab on both almanacs**, plus the data pipeline behind
the CBS one, plus a format overhaul that applies to both books
identically.

### 1a. CBS draft data pipeline (new)
CBS never logged drafts to its API in a usable historical form, so the
picks were reassembled from the site's own draft-results HTML pages
(same museum-rule scrape machinery as the transaction logs).

- **Coverage reality (CBS):** real pick ORDER + players exist only for
  **2025 and 2026** (the online drafts). 2011–2023 have players but NO
  reliable order (offline team lists ride roster order, proven rho≈0) —
  they live in the order-free **Draft Classes** section. Nothing exists
  before 2011 on any CBS surface (proven across every surface; see
  progress-doc round 8). Same-year Mini+Mega drafts (2025/2026) are
  stitched as one continuation; 2020/2021 "second drafts" are the same
  players recorded twice (deduped); 2024 is split-brain and NOT
  recoverable.
- **Identity + value:** 4,269/4,270 picks resolve (the one miss is
  2011's genuinely ambiguous Chris Young, flagged not guessed).
  Two-way value walks the mlbam spine (unified picks sum split
  pseudo-ids).

### 1b. The format overhaul (both books, identical structure)
Driven by Kyle's annotated mockup. Both Draft Recap tabs now have:
- **House column-width grid** (`25 40 40 125 75 40` then `100`+),
  documented in BRAINTHOUGHTS as a convention: 25=buffer, 40=shortinfo,
  125=full name, 75=longer-number, 100=squeezed.
- **Leaderboards:** Pts / Tm / Player / (Rd) #Pick / Δ Rank (one-decimal
  Pts). Value block B–F, an **empty buffer column at G**, busts block
  H–L; the powder header banner runs continuously B:L across the buffer.
- **Current-season board:** navy "Top Pick" super-header merged over the
  powder Pick/Team/Player trio, then decimal-free Max/Med (no Min), then
  every team's pick as a first-initial bref link (JJ/TJ/CC stay whole).
  Name cells color-graded by (invisible) season points.
- **All-time board:** re-cut to the current team shape; lists **Year**,
  Team, Player of each round's straight top pick; a second super-header
  "Each Round × Pick's Historical Median Value" over the cells; cells =
  the slot's season-**paced median**, color-graded (Med column included,
  Max excluded); Rd/Year/Max/Med/cells center-aligned. Includes the
  ongoing season, paced up. **ESPN keeper "K" round** on top (see §3d).
- **Coverage line** (CBS) is data-driven; the "prior to 2025" caveat is
  bsb-specific prose (see §3f).

### 1c. Investigations (answers, no code churn unless noted)
- **Pitcher "drift" = scoring-rule change, not a bug** (§3e).
- **Keeper K-row bug found + FIXED** (§3d) — was ranking league-wide.
- **Crosswalk gap = prospect lag** (§3g), systemic understanding.

---

## 2. File map (where to pick up)

**Extract / parse (CBS, `extract/`):**
- `cbs_ui_capture.py` — `--drafts-sweep` mode fetches every draft page
  (both server-side sort views). Cookie auth (`CBS_WEB_COOKIES`),
  content-verified, museum rule (GET-only).
- `cbs_draft_parse.py` — keyed HTML → `draft_rows.ndjson` (one row per
  table row, era-tolerant column templating).
- `cbs_capture.py` — has whitelisted (read-only) draft API endpoints
  (`draft_results/order/config`) added this session; current-season-only
  (see §3h), landed under `data/cbs_raw/bsb/2026/draft/`.

**Data provider (the STOPGAP — see MLB-90):**
- `output/cbs_draft_recap_data.py` — assembles picks from the NDJSON +
  warehouse calc points. Reads NDJSON on disk AND an intermediate model
  directly (both flagged in-code). QA harness: run it directly
  (`python output/cbs_draft_recap_data.py`) for assembly/resolution
  tables + the page-vs-calc cross-check.

**Builders / renderers:**
- ESPN chain: `almanac_data.get_draft_board` /
  `get_draft_history_boards` / `get_season_scoring_periods` →
  `almanac_logic.build_draft_tab_rows` / `_draft_board_grid` /
  `_alltime_draft_grid` / `season_pace_factors` → `almanac_render` draft
  helpers (`_draft_initial_label`, `draft_initial_text`,
  `format_draft_value_row`) → `almanac_write` draft writer
  (`_draft_label_formats`, `_draft_merge_requests`,
  `_apply_draft_board_colors`).
- CBS: `output/cbs_almanac_sheets.py` — `build_draft_recap_rows`
  (mirrors the ESPN structure using CBS's builder-side format specs),
  `DRAFT_TAB` registered in `build_all_tabs`, imports the shared
  `season_pace_factors` + `draft_initial_text`.

**Raw artifacts (MAIN checkout, gitignored):**
- `data/cbs_raw/bsb/history/ui/drafts/keyed/*.html` (42 pages),
  `parsed/draft_rows.ndjson` + `parse_summary.json`.
- `data/cbs_raw/bsb/2026/draft/*.json` (the current-season API pull).

**Tests / goldens:**
- `tests/test_cbs_draft_recap_tab.py` (5 layout tests over injected
  history — the template for CBS builder tests).
- Golden `tests/fixtures/almanac_v1_1_0/Draft-Recap.tsv` (+ the 14 team
  tabs, which the parenthetical fix touched). Re-anchor after
  intentional ESPN changes:
  `REGENERATE_BASELINES=1 pytest tests/ -m warehouse -k almanac_byte_diff`.

**Docs:** `DRAFT_RECAP_PROGRESS.md` (round-by-round + open questions),
`HANDOFF_CBS_DRAFT_RECAP.md` (incoming map), `BRAINTHOUGHTS.md`
(gitignored — width conventions + a credential-freshness wishlist item).

---

## 3. Load-bearing facts (verify before trusting; some are subtle)

**a. Value lens.** Calculated season points (universal MLB stats ×
current league scoring), switchable via a `value_lens` knob. The page's
own Total/Active Fpts are captured as alternates (2022+ only). The CBS
coverage note says "Points are calculated using current league scoring."

**b. Season pacing** (`season_pace_factors`, shared). N = median clock
over CLOSED seasons; each season weighted N/its-own-clock. Clock = a
day-equivalent: ESPN = distinct daily `scoring_period` (~184 full
season), CBS = `get_season_gameplay_days` (~180). Partial ongoing season
scales UP to a full-season equivalent. **Cells = paced median; Max +
Top Pick stay STRAIGHT (unpaced)** per "prorating shouldn't affect top
picks." Median/mean tension resolved in Kyle's favor: kept pace+median,
weighted-mean is a one-function swap if ever wanted.

**c. All-time re-cut.** Round N = each covered season's overall picks
`T(N-1)+1 … TN` (T = current team count). A 16-team-era pick #17 lands
in today's round 2. Drafted picks are re-sequenced per season (keeper
gaps removed) before the re-cut, so drafted round 1 = first player
actually drafted.

**d. Keeper "K" round (ESPN only).** Uses **real ESPN keeper tags**
(`mart_draft_board.keeper`), NOT a first-N-rounds proxy (verified: 11
teams have 5 keepers, SMEL 4, CAL & GPGP 0). Cell N = median across
teams of their Nth-best keeper's paced points (5 cells, 6–14 blank).
**Bug fixed this session** (`6fd3344`): `get_draft_history_boards` wasn't
selecting `team_id`, so keepers ranked LEAGUE-WIDE. Data-driven — the K
row only appears when keepers exist, so CBS is untouched. **Low signal
today**: 2025 was the inaugural draft (0 keepers), so the K row is
entirely 2026-paced; its cells read higher than the raw Max. Kyle likes
it and wants it kept; trivially hideable.

**e. Pitcher scoring EVOLVED — QS + IRSTR added in 2024** (documented,
intended). 2022–23 calc runs ~10–15% above CBS's contemporaneous page
Fpts because **Quality Starts (now +4) and Inherited Runners Stranded
(now +2) were NOT scored before 2024.** Proof: `page = calc − 4·QS −
2·IRSTR` fits every 2022–23 pitcher (relievers to 0.0 exactly, all
IRSTR; starters residual ~−2, all QS); 2024/25 reconcile calc/page =
1.000. It's the current-rules-across-all-eras lens working as designed —
NOT a bug. Also in the CBS-league memory.

**f. CBS coverage.** `_draft_coverage` auto-populates the "Coverage:
2025–2026" string from years that have BOTH pick order AND identified
picks (`order_tier` 'true' + resolved value). The "*CBS does not avail
draft data prior to 2025…" caveat is `_BSB_DRAFT_CAVEAT` — **bsb-specific
prose**, deliberately not universal to CBS (another CBS league might
avail older drafts). The Draft Classes section (orderless 2011–2023
players) coexists — "draft data" in the caveat means draft ORDER.

**g. Crosswalk gap = prospect lag** (systemic, ~1.9%). Only 9/480 2026
picks miss the crosswalk, all top prospects (Crawford, Clark, Condon,
Holliday, De Vries, Made, Miller, Williams, Lee). The crosswalk is a
point-in-time CBS→MLBAM snapshot built only from players in the
universal MLB feed; prospects lag it. Most haven't debuted (correctly
0); Crawford/Lee debuted after the 2026-07-09 sweep. A crosswalk
re-sweep heals the debuts; the rest stay 0 until they play.

**h. CBS auth (both ~weekly TTL, browser-minted).** `CBS_TOKEN` (API) =
the `access_token` inside the page's global player-search function (NOT
the look-alike CBSi token; network-tab alternative: filter `fantasy`,
read `access_token=`). `CBS_WEB_COOKIES` (site scraping) = the whole
`cookie:` header. Procedure + observed TTL in SETUP.md §6 + Linear
MLB-13. The draft API endpoints are LIVE but current-season-only
(`season`/`year` are decoys) — the 2026 payload is a nice structured
gain (per-pick round/overall/**team ids**/player objects + the draft
chat), landed and flagged to fold into the seasonal capture.

**i. STOPGAP → dbt.** The provider reads the NDJSON + an int model
directly (2 logged exceptions). MLB-90 tracks landing picks in
`RAW.CBS_DRAFT` → `stg_cbs__draft` → union under `mart_draft_board` so
one chain serves both leagues.

---

## 4. Linear structure (new this session)

**Project: Google Sheet Almanacs** — the OUTPUT-surface axis. Splits the
"ESPN"/"CBS" ambiguity:
- **MLB-85 ESPN Platform Almanac** / **MLB-86 CBS Platform Almanac** —
  the reusable platform-general renderers.
- **MLB-87 Buns in the Sun Almanac** (the ESPN league `espn-main`) /
  **MLB-88 Box Score Baseball Almanac** (the CBS league `cbs-bsb`) — the
  two bespoke league sheets, a **half-way-to-public staging area**;
  their changes are often bespoke and NOT universal.
- **MLB-89 Cross-Platform Google Sheets Almanac** — changes for both
  books at once.
- **MLB-90 dbt-ify the CBS draft chain** (under Ops, Debt & Hardening) —
  greenlit for the pre-2.0 "go dbt-heavy" deep clean.
- **MLB-82 User-Editable Config Design** — added a calc-vs-platform
  points toggle to the running list.

Mirrored to `docs/roadmap/google-sheet-almanacs.md` + `ROADMAP.md` per
convention. Linear team = `fantasy-league-almanac` (never touch the ROG
team). Alias workflow, comment-on-flip, and status rules per
`project_linear_roadmap` memory.

---

## 5. Open items / next steps

- **MLB-90 dbt-ification** — greenlit, the main structural follow-up
  (retire the provider stopgap; pre-2.0).
- **Crosswalk re-sweep** for 2026 debuts (Crawford/Lee) — optional; needs
  the extract pipeline (MLB-70 family) re-run.
- **Pre-2013 CBS drafts** — off-platform only (FIL's old emails /
  commissioner files). Kyle is passing these questions to his
  father-in-law (whose league bsb is). The pipeline takes a manual seed
  CSV cleanly.
- **User-facing docs** (MLB-74 family) — the explanations trimmed off the
  tab notes this session are bound for the Almanac User Guide.
- **Keeper K-row** — kept per Kyle; low-signal until a 2nd keeper season
  accrues.

---

## 6. Conventions / gotchas

- **Museum rule:** the CBS league is READ-ONLY forever. Every CBS touch
  is a GET, content-verified (never trust HTTP 200), politely paced.
- **Dev-sheet merge gate:** render to the DEV sheet
  (`generate_almanac_sheet.py --league <key>`; or the targeted
  single-tab `cbs._write_tab` / ESPN `_replace_draft_tab` scratch
  runners) and ALWAYS prove rerun-idempotency with a write-twice check.
  Never `--prod` without intent.
- **Golden re-anchor** after intentional preview changes only
  (`REGENERATE_BASELINES=1 …almanac_byte_diff`); the two writers mirror
  rather than share (ESPN computes colors writer-side; CBS takes
  builder-side specs — don't try to unify them).
- **Commits:** first-person technical, NO AI-attribution trailers.
  BRAINTHOUGHTS skim at every push. Board changes mirror to
  `docs/roadmap/` in the same commit.
- **Branch is local-only** and origin is public — push deliberately
  (dev remote is the safe backup).
