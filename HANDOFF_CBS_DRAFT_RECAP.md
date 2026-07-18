# HANDOFF: CBS Draft Recap (next session)

Kyle is bringing the specific product requirements; this doc is the map
of what exists, what to reuse, and the traps already sprung once.
Written 2026-07-18 at the end of the advanced-standings sprint
(WALKBACK_PROGRESS.md rounds 1-14 = the freshest style/context read;
branch `claude/modest-montalcini-3af8c4`, tip pushed).

## Mission shape

Build a CBS Draft Recap mirroring ESPN's Draft Recap tab. CBS-bsb facts
that bound the problem:

- **CBS never logged drafts.** The transaction log's earliest signal is
  adds/trades; the walk-back recovers OPENING ROSTERS implicitly
  (players on the year-end anchor with no in-season acquisition). That
  recovery gives you who STARTED each season's roster -- it does NOT
  give pick order, round, or draft position. If a real recap needs pick
  sequence, the data must come from somewhere new -- almost certainly
  the CBS site's own draft-results pages, i.e. the HTML scrape/fill
  machinery again (below).
- **No keepers in bsb** (league facts memory) -- the ESPN board's
  keeper-sort/labels collapse away. One less axis.
- 16 teams, 2001-2026, canonical-franchise identity via `dim_franchise`
  (roll up member ids builder-side; render actives; the finishes matrix
  in `build_standings_rows` is the reference implementation).

## The ESPN chain to mirror (read these first)

RAW -> staging -> mart -> data fn -> builder -> writer:

1. **RAW.DRAFT_PICKS** -- append-only VARIANT snapshots (one array of
   pick dicts per extract), from the espn-api wrapper's `league.draft`
   at extract time. `extracted_at` for latest-snapshot selection.
2. **`dbt_league/models/staging/stg_draft.sql`** -- latest snapshot per
   (league_key, season_year); one row per overall_pick with round_num,
   round_pick, player_id, player_name, team_id, keeper. ALREADY
   league_key-grained -- the shape is platform-general even though only
   ESPN feeds it today.
3. **`marts/reporting/mart_draft_board.sql`** (view) -- picks + latest
   team labels + the player's full-season calculated production
   (COALESCE 0 for never-played = a genuine zero for bust ranking).
   Value leaderboards deliberately live in the OUTPUT layer, not here.
4. **`output/almanac_data.get_draft_board(season_year)`** -- adds
   points_rank + value_delta (overall_pick - points_rank) query-side.
5. **`output/almanac_logic.build_draft_tab_rows`** +
   `build_draft_board_color_grid` (+ `_draft_sorted_columns`,
   `format_draft_board_cell` in almanac_render) -- the round x team
   board, keeper-sorted columns, per-cell season points feeding the
   writer's red->white->green board scale.
6. **Writer**: the ESPN draft tab pass in `almanac_write` paints the
   color grid; `DRAFT_TAB` named in almanac_render; preview tab
   'Draft-Recap' wired in `generate_almanac_sheet.py`.

## Reuse doctrine (Kyle's standing rules, sharpened this sprint)

- **No platform-specific tables queried directly as exposures.** The
  output layer reads league_key-grained shared surfaces. Two blessed
  routes, both precedented:
  (a) UNION AT THE INTERMEDIATE -- `int_player_daily` unions the ESPN
      branch with `int_cbs__player_daily` into ONE
      `fct_player_daily_performance` (MLB-72). For drafts: land CBS
      picks so `stg_draft`'s downstream serves both, either by feeding
      the same RAW/staging shape from a CBS extract or by a
      `stg_cbs__draft` branch UNIONed before the mart.
  (b) PROVIDER FUNCTIONS AT THE OUTPUT SEAM -- CBS team tabs render
      through the SHARED `build_team_history_tabs` because
      `get_cbs_team_history_data` emits ESPN's exact row contract
      (knobs: optimal_team_fn / title_fn / lineup_data / best_seasons_fn).
      For the recap: aim `build_draft_tab_rows` at a CBS provider
      emitting `get_draft_board`'s row contract. Renaming/reshaping the
      shared builder is fine; forking it is not.
  - One logged exception to the exposure rule: `get_mlb_affinity` reads
    `int_cbs__player_game_points` (the only home of MLB team-of-game);
    mart promotion is a wishlist item. Don't add a second exception
    without the same explicit note.
- **Identity is id-first with name-key fallback.** Scraped draft names
  will need the same seams the walk-back used: `cbs_name_key` macro +
  SUFFIX NORMALIZATION (the Vlad Jr. lesson: one report drops Jr/IV,
  another keeps it -- one player became two), `dim_player_identity`
  (MLB-81) for cross-source resolution, split two-way pseudo-ids
  (900/901) absent from position universes, ambiguous-name flags (the
  Will Smith / Luis Garcia class) -- FLAG, never guess.
- **dbt conventions**: schema docs + grain/enum tests ship WITH every
  new model (the walk-back's 33/33 pattern); seeds change ->
  `dbt seed --full-refresh`; catch-all totals over per-stat pivots;
  var-toggle any platform-specific filter; append-only RAW with
  extracted_at; catalog + Linear + docs in the same commit as board
  changes (roadmap conventions memory).

## The HTML scrape/fill playbook (the part Kyle flagged)

Everything lives in `extract/`:

- **`extract/cbs_capture.py`** -- authenticated live-capture of CBS
  pages/payloads (see how it walks `overall_standings.divisions`).
  Auth is TOKEN-based; the login has reCAPTCHA, so tokens are minted
  manually and passed in -- do not try to automate login.
- **`extract/cbs_ui_parse.py`** -- HTML -> rows parser + RAW DDL. The
  year-by-year dashboard's "Final Standings" card parser (division
  blocks -> `division_name` per team) is the template: regex-tolerant
  cell extraction, `_clean()` normalization, explicit RAW schema.
- **Hard-won rules, in blood:**
  1. **Verify by CONTENT, never HTTP 200.** CBS serves happy empty
     pages. Every sweep asserts row shapes/counts before writing.
  2. **The obvious URL params are DECOYS.** Historic intra-season data
     came via `point=YYYYMMDD` (rosters) and `period=N` (standings),
     NOT the parameters the UI suggests. Expect the draft pages to have
     their own non-obvious year switcher -- the rules page and rosters
     both had one. Probe with content diffs across param guesses.
  3. **Season-grain history is real; per-game only 2007+** -- if draft
     pages exist per season, they may not exist for ALL seasons. Grade
     coverage honestly (era-keyed provenance labels are the house
     pattern -- see get_provenance_mix and the team tabs' Lineup Data
     lines).
  4. **Long sweeps run DETACHED**: PowerShell `Start-Process` with a
     disk log + idempotent resume (Bash background shells die with the
     Claude Code process -- this killed an extract at 87/2214 once).
     Pace projections first; add `--min-season`-style guards so you
     don't fetch what can't attribute.
  5. **Dual-source verify when two routes exist** (MLB-54 pattern): the
     API log vs UI report full-outer-join graded 746/748 with the two
     misses EXPLAINED (a pre-season trade the UI structurally omits).
     If a current-season draft API surface exists alongside scraped
     pages, run the same cross-grade.
  6. **The UI logs trades ONE-SIDED** (receiver's trade_in) -- any new
     UI report may have the same asymmetry; look for counterparty
     columns before assuming completeness.
  7. RAW tables: append-only, `source_path`/`extracted_at` style
     lineage columns (see stg_cbs__ui_transactions's txn_row_key /
     row_seq for dedup-safe keys).
- **Where draft data might live** (investigation order):
  1. The CBS year-by-year dashboard (same surface as the Final
     Standings card) -- check for a draft-results card/page per season.
  2. A dedicated draft-results page under the league site with a year
     switcher (probe the non-obvious params).
  3. Current-season only: the fantasy API layer (remember: fantasy
     layer is current-season-only, but intra-season history hides
     behind the decoy params).
  4. If pick ORDER genuinely doesn't exist anywhere pre-X: the
     opening-roster recovery (int_cbs__roster_stints open_channel IN
     ('opening','lineup_opening','lineup_evidence')) still supports a
     degraded "opening day roster" recap -- scope with Kyle before
     building that fallback.

## Output/Sheets conventions (current state, post-sprint)

- Builders emit `(title, rows, formats)`; the CBS writer's format spec
  kinds: plain cellFormat, `{'gradient', 'range'|'ranges'}`,
  `{'hide_rows'}`, `{'hide_cols'}`, `{'checkboxes'}`, `{'chart'}`,
  `{'merge'}`, `{'runs'}` (textFormatRuns -- emoji are 2 UTF-16 units).
  `_stale_style_state_requests` wipes rules/groups/charts/validations
  before restyling -- ALWAYS prove rerun-idempotency with a write-twice
  check (the scratch pattern lives in this sprint's walkback rounds).
- ESPN board color grid vs CBS: the CBS writer paints gradients from
  BUILDER-side specs; the ESPN writer computes from rows. Follow each
  writer's own architecture -- they deliberately mirror, not share.
- House visual system (both books now): navy section bands (unified
  width = widest band), italic fontSize-9 notes under bands, pale-blue
  italic subtitle on row 2, frozen title band, single blank buffer rows,
  the decimal rule (0dp unless a column's average < 10, then 1dp),
  light-gray true-zero cells, per-club/row max bolds where "who leads"
  matters. ESPN L/R splits share the U-column divider; CBS has no
  equivalent (Kyle's call). ASCII `--` in note text; keep en-dash year
  ranges.
- Team labels: CBS = full franchise names, ESPN = abbrev + owner (the
  known 1-column asymmetry, BRAINTHOUGHTS-logged, standardization
  deferred).
- Renders default to DEV sheets (`generate_almanac_sheet.py --league
  cbs-bsb`; `--no-sheets --preview-dir` for TSVs). Kyle's eyeball of
  the dev sheet is the merge gate. Targeted single-tab rewrites via
  `cbs._write_tab(...)` keep iteration cheap (see the scratch rerender
  script pattern in the walkback).

## Ops

- Interpreter: repo-root `.venv`
  (`C:\Users\kyled\projects\espn-league-manager\.venv\Scripts\python.exe`);
  `.env` lives at repo root and resolves from the worktree via dotenv's
  upward search -- run with the worktree as cwd.
- Suites: `pytest tests/ -q` = 229, no warehouse. Warehouse-marked
  suite (`-m warehouse`) is FULLY GREEN as of this push -- keep it
  that way; the byte-diff goldens re-anchor with
  `REGENERATE_BASELINES=1 pytest tests/ -m warehouse -k
  almanac_byte_diff` (only after intentional ESPN preview changes).
  New CBS builder tests: monkeypatch `get_franchise_map` and feed
  layout fixtures -- `tests/test_cbs_standings_tab.py` is the template.
- Commits: first-person technical, NO AI attribution trailers. Bundled
  phase commit + doc commit. BRAINTHOUGHTS skim at every push.
  WALKBACK-style progress doc per sprint, appended per round, committed
  at checkpoints.
- Linear: team fantasy-league-almanac only; every assignee flip gets a
  comment; board changes mirror to docs/roadmap/ in the same commit.

## Adjacent open threads (don't trip on them)

- Affinity mart promotion (int-read exception) -- wishlist.
- DH/UTIL slot-class merge for records -- wishlist (dim layer, not
  output).
- Champion-definition toggle + unified statline builder -- wishlist.
- ESPN division extraction doesn't exist (CBS-only); irrelevant to the
  recap but noted in BRAINTHOUGHTS if drafts surface division info.
- 2025 ESPN transaction log: looked-and-blocked (leagueHistory rejects
  the topics filter) -- same class of wall a CBS draft-history hunt may
  hit; document verdicts with the same looked-vs-blocked clarity.
