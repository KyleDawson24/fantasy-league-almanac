# MLB-63 Walk-Back + Almanac v2 — Live Progress Log

## MLB-64 CONTINUITY INGESTION — warehouse (2026-07-15)

Kyle took a first pass at the sheet (3 lineage links, 3 owner-alias merges,
324 owner-year fills) and green-lit threading canonical franchise **everywhere**
(the plumbing is stable; the links are just editable seed data).

- [x] **`output/harvest_continuity_sheet.py`** — reads the filled sheet →
      three dbt seeds. Union-find on `Same As` (mutual/chained pointers
      collapse to the earliest-id anchor; a current-era owner slug wins the
      owner anchor). Fuzzy header match (Kyle already retitled a header + added
      a preferred-abbrev column). Re-runnable after every editing pass.
- [x] **Seeds** (override-only, varchar-pinned so empty override columns don't
      infer numeric — that bug cast 'Firefly Lake Vandals' to a number):
      `cbs_franchise_lineage` (3), `cbs_owner_alias` (3), `cbs_owner_by_year`
      (424, the historian's Owner 1/2/3; downstream COALESCEs with
      `stg_cbs__ui_rosters.owner_name`).
- [x] **`dim_franchise`** (marts/core, view) — franchise_id →
      canonical_franchise_id (earliest anchor) + canonical name/abbrev; unlinked
      + #### map to self. **34 ids → 31 canonical franchises.** 5 dbt tests
      green (unique grain, not-null canon/name).
- [x] **Owner models LANDED (per-season, all-time).** Harvest now writes a
      COMPLETE `cbs_owner_by_year` (his Owner 1/2/3 where entered, else the
      split roster-page owner) so no SQL co-owner splitting is needed.
      `int_cbs__team_owner_season` (new) resolves names → canonical owner ids
      (current owner matched by display to keep its seeded slug; else slugged;
      then `cbs_owner_alias` collapses Dave/Desmond Foster). `stg_cbs__team_owners`
      re-sourced from it → grew from 16 current-era rows to **328 team-seasons**;
      `dim_owner` grew **19 → 46** CBS owners (ESPN untouched at 16; a
      `seen_name` fallback displays the historical owners the nickname seed
      doesn't cover, first-token → first_name for co-owner comma joins).
      Verified: current + ESPN displays unchanged, Fulton one owner across
      13+30 all years, 173 downstream tests green.
- [x] **Made platform-agnostic (Kyle's call).** Override seeds shed the `cbs_`
      prefix -> `franchise_lineage`, `owner_alias`, `team_owner_by_year`
      (league-keyed rows, matching the shared `owner_nicknames`/`player_alias`
      convention; CBS is just the only league with rows loaded). `dim_franchise`
      now reads its observed input from a platform-general seam
      **`int_franchise_registry`** (the "what your league's history makes
      available" layer) rather than `cbs_franchises` directly -- a new platform
      adds ONE union branch there + optional lineage rows, no override required
      to flow through. The owner CANON (dim_owner/dim_team_owner) was already a
      platform union; only `int_cbs__team_owner_season` stays CBS-specific
      (CBS serves owner NAMES, not ids -- legit platform adapter). Behavior
      unchanged (34->31, 23 tests green).
- [ ] **THREADING (in progress) — canonical franchise through the almanac.**
      Doing it in the CBS RENDERER (cbs_almanac_sheets.py) by joining
      dim_franchise, NOT in shared marts -- so ESPN (separate renderer) is
      untouchable and no shared model changes.
  - [x] **Season Finishes matrix** rolled up by canonical (`get_franchise_map`
        + `_canon`): Foster's Folly (13+30) and Kimball Drives (22+28) are now
        ONE active row each spanning both eras; Windmill Haymakers (11+23) one
        defunct row; 34 ids -> 31 rows. Verified.
  - [x] `_franchise_owner_labels` -> canonical bridge (dim_franchise) + real
        per-lineage owners from dim_team_owner history. Drops the abbrev-inherit
        hack (false-linked 14/17); id 14 now shows Gideon Osborn (its own 2008
        owner). Records Owner column is correct across renames. (Per-SEASON
        owner on a season record = the team-pages session's refinement.)
  - [ ] Team pages span a franchise's ids; records/team-holder by canonical.
  - **ESPN byte-diff note:** the golden shows ONE pre-existing drift cell (a
        reliever's 158->159 / 3.86->3.87, the documented float/rebuild-order
        class). NOT from this CBS-only work (separate renderer; no ESPN player
        facts rebuilt this session). Needs a `REGENERATE_BASELINES=1` re-anchor
        when Kyle's ready -- flagged, not done unilaterally (his ESPN caution).

## MLB-64 CONTINUITY MAPPING SHEET (2026-07-14)

- [x] **`output/build_continuity_sheet.py`** — a platform-agnostic generator
      for a hand-to-a-human franchise/owner **override sheet**. Non-technical
      fill-or-ignore; blank always = "observed value is correct". CLI:
      `--league <key> [--sheet-id <id|url> | --preview-dir <dir>]`
      (preview never touches Sheets). Reuses the almanac writers' cached OAuth
      token (spreadsheets scope — can't mint a sheet, so the target must
      pre-exist and be user-owned).
- [x] **Rendered to Kyle's sheet** `1fMCSSRvpE-2HwEOf3OuSPsvsbootWq1bWI-2zVvLx94`
      (his Drive, his to share with the FIL). Four tabs: READ ME FIRST · Teams
      (34) · Owners (19) · Team-Owner by Year (411). Grey = read-only evidence
      (warning-only protected), yellow = fill/ignore.
- **Owner history from the roster pages (Kyle, 2026-07-15):** the year-end
      roster report header carries the owner (`Team Name - Owner`), and
      `cbs_ui_parse.py` **already extracts it** into
      `stg_cbs__ui_rosters.owner_name` (populated 2007+, blank 2003-06) —
      nothing downstream consumed it. So the "sweep" is a warehouse query, not
      an HTML scrape. Wired into the generator: (1) bridge **"Owner(s) on
      record"** is now OBSERVED for 2007+ — 316/411 rows (76%) pre-filled, so
      the historian only fills pre-2007; (2) the **Owners tab grew 19 → 49**
      (all-time, co-owners split, casing normalized via `_split_owners`/
      `_norm_owner`); (3) **owner continuity now drives the hints** (surname
      match): 30→13 (Fulton), 28→22 (Kline), 23→11 (Savage/Colton/Landon revival)
      auto-suggest; **id 26 resolved as a co-owner SPLIT** — Kendrick→31,
      Keating→32 (both partial continuations, not one lineage); 14/17 stay
      distinct (shared owner Osborn noted). Also nailed the ticket's mystery:
      **2020 id 1 = Julian D. Sherman + Preston Larkin** ("Ashen Tyrants").
      *Warehouse-side follow-on (MLB-64 ingestion):* `dim_owner`/
      `dim_team_owner` should consume `owner_name` for per-season history — now
      trivial (the data's already in staging), no re-extraction.
- **Robustness follow-ups (Kyle caught both):** (a) **live season included** —
      `stg_cbs__ui_standings` stops at the last completed year, so 2026 is
      unioned from `stg_cbs__rosters` (latest captured name/team); without it a
      rename made THIS year is invisible (id 1 → Firefly Lake Vandals 2026 now
      shows). (b) **fill cells are dropdowns, not free text** — data-validation
      (strict=False, so unseen historical owners can still be typed); the
      bridge's co-owner field is split into Owner 1/2/3 columns with an
      "Owner(s) today" read-only hint. (c) ported the almanac's `_sheets_call`
      backoff — an un-retried transient 503 on a `clear()` had left the sheet
      half-written.
- **Design (Kyle-approved):** `Same As (Canonical)` resolves downstream as
      `COALESCE(same_as, league_key || '::' || id)` — blank auto-namespaces
      (every league's "1" stays distinct), an earlier id merges within a league
      (earliest = anchor), a **shared free-text label stitches across
      leagues/platforms** (the ESPN→CBS migration superpower, MLB-8-adjacent).
      Teams.`Same As` pre-filled ONLY for unambiguous remints (30→13, 28→22 —
      the sat-out-2020 pattern); overlaps (14/17) and forks (26→31/32) left
      blank with hints. Findings surfaced: id 1 present in 2020 as "Angry
      Tyrants" (contradicts the ticket's "id 1 absent 2020").
- **Tab-3 autofill levers** (Kyle's question — how much of the bridge fills
      itself): (1) DONE — today's owner propagated across each active
      franchise's span (the "assumed" column). (2) TODO — once Teams links a
      retired id to an active one, back-propagate that owner. (3) THE unlock —
      parse year-end roster-title owners (2008+) for real per-season custody;
      shrinks the manual residue to pre-2008.
- **Next (the ingestion side, separate from the FIL sheet):** a `dim_franchise`
      resolver applying the filled seed (`COALESCE` rule above) + growing
      `stg_cbs__team_owners` from current-era to per-season; the roster-title
      owner parse (lever 3).
- **NOTE — uncommitted WIP alongside this:** `cbs_almanac_sheets.py` +
      `almanac_render.py` carry the in-progress Lineup Slot Records rework
      (`_ROSTER_SLOTS`), rate-records Details (`_rate_component_detail`), the
      ≥1.000 rate fix (1.422 OPS), and the bref period-strip for middle-initial
      names — left uncommitted on purpose; not part of the continuity commit.

## ALMANAC V2 (2026-07-13, per Kyle's re-sequenced approval — union first)

- [x] V2-A: **Fielding sweep** — `mlb_stats.py --fielding` (yearByYear
      group=fielding, one call/player over the 3,852-id crosswalk
      population, idempotent) launched DETACHED; log:
      `data/mlb_stats/fielding_extract_20260713.log`.
      **BONUS DISCOVERY that saved a second sweep: the gamelog files ON
      DISK already carry per-game `positionsPlayed`** (the loader just
      never projected it) — so the "10 games this year" achievement
      DATES need no re-fetch: new loader family `gamepos` re-walks the
      same files → RAW.MLB_GAME_POSITIONS (1,931,131 rows / 3,855
      players, loaded). Season-grain fielding remains the authority for
      totals + pre-league seasons (the 2001 season's "20 games last
      year" reads 2000).
- [x] V2-B: **Shared eligibility model** — `int_cbs__eligibility_windows`:
      the captured rule verbatim ("primary position, plus positions
      played 20 games last year or 10 games this year"; "Everyone is
      eligible at DH") as date-scoped AFTER-ACHIEVEMENT windows
      (primary + prior-year-20 from opening day; in-season-10 from the
      10th game's date, inclusive). Primary = DERIVED estimator
      (prior-season argmax fielding games, current-season fallback) —
      CBS serves no historic primary label; graded vs the 2026 captured
      per-day eligible_positions (grade below). DH-for-all = slot
      semantics, not stored windows; arrays floor to ['DH'] (matching
      CBS's own display for fieldless hitters — and the Ohtani canary:
      captured 900='DH' / 901='P', which the crosswalk scope guard
      reproduces exactly).
- [x] V2-C: **MLB-72 union layer LANDED** — `int_cbs__player_daily`
      (CBS day-grain: attribution × engine, game→day aggregation, the
      eligibility arrays, franchise names) UNION ALLs into
      `int_player_daily`; the shared fact family
      (fct_player_daily_performance → fct_player_position_pts) now
      serves both leagues. Shared columns added both branches:
      player_key (the cross-league grain — ui-only synthetics have no
      numeric id), game_date, active_weight, provenance. New lenses on
      fct_player_position_pts: weighted_active_pts (the CBS Best-Lineup
      axis; ≡ active_pts wherever state is known) + rostered_pts (the
      bench-ranking axis). Weekly facts take a matchup_period-is-not-
      null guard (weekly grain needs platform periods; no-op for ESPN).
      ESPN byte-neutrality verified: unit suite + almanac byte-diff
      goldens (results below).
- [x] V2-D: **Career-totals mart** — `mart_player_career_records`
      (MLB-69 accumulation axis over int_cbs__player_season_stats,
      top-10 per stat, seasons-span columns; sibling conventions of the
      season book).
- [ ] V2-E: **Renderer rebuild** (`output/cbs_almanac_sheets.py`) per the
      approved blueprint — nav-first Home (#gid links, glossary,
      All-League boards Season + All-Time), Records = best season ×
      career, Standings = 2026 arc + 25y finishes, team pages = Best
      Lineup current × all-time (slot template C/1B/2B/3B/SS/OF×3/DH/U/
      P×9) + bench by rostered points; ACTIVE-franchise tabs only;
      franchise_id-keyed with the aggregation isolated for the MLB-64
      owner re-key.
- [x] V2-F: Dev render (`--league cbs-bsb`) — 19 tabs on the dev sheet
      2026-07-13; Kyle's first review same day: "promising, much better
      shape than v1" + the make-it-ESPN-exact directive below.
- [ ] **V2.1 (Kyle's review round 1, 2026-07-13): Home page ESPN-exact,
      via the SHARED builders — approach (a) chosen ("get something
      quickly", with the (b)-refactor gains documented in BRAINTHOUGHTS
      Wishlist).** Landed this round:
      * **Owner identity for CBS** (Kyle: "every team MUST have an owner
        and ideally a sturdy owner id"): minted `cbs-` owner-id slugs in
        the new `cbs_team_owners` seed, names on the SHARED
        owner_nicknames seed, flowing through the SAME dim_owner →
        dim_team_owner chain ESPN uses. Multi-owner display per Kyle's
        spec: first names comma-joined ("Bob, Sanford" — Hot Dog
        Junkies; "Jim, Sam"; "Patrick, Travis"). Current-era only;
        history lands with MLB-64.
      * **Franchise registry seed** (`cbs_franchises`, all 34 ids):
        curated abbrevs because CBS's own capture abbrevs are UNUSABLE
        as identity (T2 and T3 each appear on two teams; AH/KR are
        stale pre-rename). Continuity pairs (13/30 Fulton, 22/28 Kline,
        14/17 Bent Spokes, 26/31 VCF) deliberately share an abbrev.
        Abbrevs are proposals — tweak the seed freely.
      * **Union contract display columns filled**: the engine now carries
        H/AB/HBP/SF/L as unpriced display context (slash-line inputs),
        and CBS daily rows carry pro_team (captured MLB abbrevs),
        team_abbrev (seed), owner_name (current era; era-honest NULL on
        history) — no more contract-lying zeros/nulls.
      * **Home boards through the ESPN builders**:
        format_all_league_team_row_with_deviation + _deviation_by_slot
        (+ a player_key identity fix so ui-only alternates surface) +
        _merge_home_bands + home_nav_link. Season-to-Date board =
        ESPN's exact 10-column shape (Slot|Team|Player|Fantasy
        Team|Owner|Points|Slash|Stat Line|Total-Pts Best), plain Points
        per Kyle (season-long numbers, no boxscore). All-Time board =
        same shape with Kyle's column semantics: MLB Team =
        current-or-blank (falls out of MAX_BY(pro_team, game_date) —
        retired players' latest rows predate the capture era), Fantasy
        Team = the player's franchises by weighted active points capped
        at 3, Owner blank until MLB-64. Deviation lens = new
        points_type='rostered' (≡ ESPN's 'all' where states are known;
        additionally sees CBS's estimated era). Deviation label drops
        "& FA" (no FA lens in CBS attribution — the one wording
        deviation). Styling mirrors ESPN's restrained Home set
        (bold-14/pale-blue/navy board headers/K+O number formats/ESPN
        column widths) — the navy-everywhere v2 styling that read as
        "random formatting noise" is gone.
      * **platform_points question (Kyle #1) answered in code archaeology,
        no change needed**: the board field named platform_points has
        carried CALCULATED points since v1.1.1 (deliberate rename-debt,
        tracked in BRAINTHOUGHTS) — so "use calculated where platform
        ought to be" is already the design; CBS rows flow the same path.
      * Records/Standings/team pages: NOT touched this round (Kyle:
        "let's start there" = Home first).
      * **Golden re-anchor, diagnosed before re-anchoring**: the byte-diff
        + BBCode goldens drifted mid-round — root cause was NOT code: the
        weekly ESPN extract ran 07:55 EST and re-landed scoring periods
        95-103 with ESPN's overnight stat corrections (Abreu 139→138.1,
        the Rocchio/Montgomery value-board reorder, League-This-Week
        288.5→288.4), racing between the 04:31 EST baseline regen and
        this round's rebuild. Verified: every fixture delta is a week-14
        row or its season rollup, and ZERO CBS strings appear in any ESPN
        fixture diff (the union stays clean). Baselines regenerated;
        3/3 warehouse goldens green.

- [ ] **V2.2 (Kyle's review round 2, 2026-07-13 — Home FINISH):** all
      render-side except the seed abbrevs:
      * **Team of the Week board** at the TOP of the right band (his
        lean: Week / Season / All-Time). Lightweight — trailing 7 days
        (Jul 1–7), weighted-active lens, date-windowed candidate query
        against the daily fact directly (fct_player_position_pts is
        season-grain for CBS); no bench, no deviation.
      * **Bench/reserve spots**: 11 (reserve count) on Season + All-Time
        via the `_CBS_BENCH_SLOTS` knob (BE 1..11 = best players not in
        the starting 19); week omits them. Flippable per his "y/n" ask.
      * **All-Time active/retired split**: ACTIVE (currently rostered)
        players → current abbrev + Owner; RETIRED → top-3 career
        franchises by active pts (gray) + blank Owner. Years-of-Service
        column ("14: 2012–2018, 2020–2026", font 8) REPLACES the
        deviation on all-time; also threaded onto team-page all-time
        lineups. Points render whole (rounded value + '0' format).
      * **Glossary** → "Points Glossary & Documentation" (+ Wasted
        Points) + a "Stat sources" table: From Mar 25 2026 (3%) /
        2001–2003, 2021–2025 (27%) / 2004–2020 (71%), from the
        provenance mix; estimated row references the Almanac User Guide.
      * **Seed abbrevs**: 34 → MATT (owner first name — the ambiguous
        default), 4 → JUNK. Seed = the abbrev-request collection point.
        Shared abbrevs are DISPLAY-ONLY (no record aggregation — MLB-64).
      * **Almanac User Guide ticket tree**: MLB-74 + MLB-75..79.
      * Answered Qs: (1) calc_ was always the lens — only the
        `platform_points` FIELD NAME is stale; (2) CBS serves NO owner
        member-id, the `cbs-<name>` slug is synthetic + swappable; (3)
        T2/T3 were CBS's OWN colliding capture abbrevs.
      * OPEN interpretation flags (all reversible): retired Fantasy Team
        grayed; Years column at N (freed by dropping all-time deviation),
        not O; "this week" = trailing 7 days; bench = best weighted-active
        leftover.
- [ ] **V2.3 (Kyle's review round 3, 2026-07-13):**
      * **Team of the Week → Team of the MONTH** (most recent completed
        calendar month = June 2026). Root cause: CBS periods carry NO
        date boundaries anywhere (standings track period ids + cumulative
        points, never day windows) and roster captures are DAILY (105
        distinct dates, not weekly) — so a "period X" team has nothing to
        date-scope against. Month is his offered fallback; clean +
        honestly derivable.
      * **Bench lens → TOTAL (rostered) points**, universal with the team
        pages (Kyle: "starters by active, benches by total"). Confirmed
        4.1: weighted_active INCLUDES the estimated-era fractional active
        production (2004-2020 active_weight averages ~0.70, not 0/1).
      * **Bench labels "BE 1..11" → "BE - Pos"** (ESPN team-page style);
        Pos = primary/current display position (MAX_BY(position,
        game_date)); lineup_slot repointed to the discipline so bench
        pitchers render W-L/ERA not an empty batting slash;
        platform_points carried across so bench Points populate.
      * **FLAGGED for Kyle**: the all-league bench comes out ~10/11
        PITCHERS — pitchers own the highest raw totals, so "bench by
        total points" league-wide is pitcher-dominated. Faithful to the
        spec; surfaced in case he wants position balance or a different
        all-league bench lens.
      * **Kyle's breakage list**: he flagged "a small list of breakages"
        and asked whether to send now — I said send them now; awaiting.
- [ ] **V2.4 (Kyle's review round 4, 2026-07-13 — Home polish):**
      * **Team of the Month → RUNNING with an 8th-of-month rollover**:
        from the 8th on, the current month as it accrues; in a month's
        first week, retrospect on the previous completed month. THE one
        deliberately-live board — reads `date.today()` (NOT just
        warehouse state) so it turns over with the calendar ("feels
        alive"). Window caps at the latest game date; steps back if the
        chosen month has no data. Label carries "(rolls over on the 8th
        of each new month)". Determinism note: this board is
        intentionally non-deterministic; CBS has no goldens so nothing
        breaks. Today (Jul 13, past the 8th) → July 2026 running.
      * **Month board gains the Total-Pts Best deviation**
        (get_window_lineup parameterized weighted=True/False).
      * **Blank buffer row** between starters and the reserve bench.
      * **Retired Fantasy Team list → font 8** (+ existing gray).
      * **McCutchen probe — VALIDATED, no bug**: he's legitimately the
        #3 all-time OF by weighted-active (Trout 6338 / Beltrán 6136 /
        Cutch 5966 / Betts 5830 / Holliday 5249 … Ichiro #8 at 5007 /
        Braun way down at 4053). Longevity + a power/OBP/SB profile that
        scores well here. NO data lost to defunct franchises — the
        league-wide sum spans every team_id, and the leaders each touched
        10-13 franchises (proof the aggregation is complete). Ichiro's
        singles/steals game scores low in this HR/RBI-weighted league.
      * **Column widths**: the CBS Home `_HOME_WIDTHS` already MIRROR
        ESPN's `_apply_home_tab_dimensions` exactly (same col→px). If
        specific columns still look off vs ESPN, need the specifics.
- [ ] **V2.5 (Kyle review round 5, 2026-07-13):**
      * **Active-star grayed-out report (Goldschmidt/Sale/Altuve)**:
        COULD NOT REPRODUCE on current code — all three (and every
        rostered player) render ACTIVE, verified 4 ways (repro, prev9,
        full-board mismatch sweep, name-match sweep = 0 mismatches).
        Likely an earlier sheet state. HARDENED anyway: get_current_
        rostered now also returns an unambiguous-NAME index, so a
        rostered player whose all-time board key is a ui-only synthetic
        (their history) still reads active. Ambiguous names excluded
        (Will-Smith guard). No output change on current data.
      * Column widths L (Slash) 125 / M (Stat Line) 250; bench "BE - Pos"
        slot-label cells font 8.
- **NEXT BIG PIECE — RECORDS PAGE (design engaged, NOT yet built,
  awaiting Kyle's confirmation on the column/grain fork):**
      * The auto-catalog machinery ALREADY EXISTS and is data-driven:
        `get_scored_record_specs()` builds the record list from
        dim_stat.is_record_candidate + "does the league score it"
        (scoring-settings join) + dim_stat.auto_tracked overrides. So
        records auto-adapt per league — CBS will show ITS 16 scored
        categories, ESPN its own, same code. ONE wiring gap:
        stg_scoring_settings is ESPN-only; CBS scoring is in
        stg_cbs__scoring_settings — the catalog join needs the CBS
        scoring unioned in.
      * mart_stat_leaderboard (the record source) is WEEKLY-grain +
        ESPN-only (fed by the weekly active facts). CBS has no weekly
        grain → needs NEW season-grain + owner-grain record leaderboards.
      * THE CONFLICT Kyle flagged: earlier blueprint = "PLAYER records
        only: Best Season × Best Career" (already built:
        mart_player_season_records + mart_player_career_records + the
        Records tab). NOW Kyle says columns = "by SEASON all-time × by
        OWNER all-time" — which implies TEAM/OWNER grain, not player,
        and swaps player-career for owner-aggregate. Asked him to
        confirm the two columns + grain before building.
- [x] **RECORDS PAGE v2 BUILT (Kyle's round-5 spec, 2026-07-13):**
      ESPN-shaped, auto-cataloged, rendered + dev-written.
      * **Auto-catalog** (`get_cbs_record_catalog`): the record set derives
        from dim_stat.is_record_candidate × CBS scoring settings +
        auto_tracked — CBS surfaces its own scored categories from the
        same machinery ESPN uses. 19 candidates catalog cleanly.
      * **Two lenses**: Best Season (top single team/player-season
        all-time) × By Owner (franchise career totals, current-owner-
        labeled top-3 list "Owner: total · …", the MLB-64 caveat).
      * **Both sections** (Kyle): Player Records + Team Records, hitting
        then pitching; Score Records (points) on top. TEAM records carry
        a **contributors** detail (top-3 players behind the team-season,
        e.g. HDJ 1,920 K 2017 → "Chris Sale: 308, …").
      * All leaders from ONE pass over the attributed union fact
        (best team-season, best player-season, player-team-season for
        contributors, franchise-career). db.py lowercases result keys —
        access stat columns as stat.lower() (bug caught + fixed).
      * FLAGGED for Kyle's review: (1) covers the SCORED stats the union
        fact attributes; marquee non-scored overrides (HR/XBH — Bonds 73)
        need the season-stats attribution path (follow-up). (2) by-owner
        totals are the ROSTERED-total lens (incl bench) → LARGE numbers
        (269k career pts); active-lens is an easy swap. (3) player Best
        Season uses attributed (rostered) production for one-source
        consistency vs the earlier all-production record book.
- [ ] **RECORDS v2.1 — ESPN-shape REBUILD (Kyle round 6, 2026-07-13):**
      He flagged v2's shape as a wrong blueprint; rebuilt toward the ESPN
      Records layout he shared.
      * **FORMAT** now mirrors ESPN: Record | [Season: Holder|Owner|Value|
        Year|Details] | gap | [All-Time Total: Holder|Owner|Value|Yrs|
        Details]. "Season" = best single season (replaces ESPN's current-
        season/weekly); "All-Time Total" = best career accumulation
        (replaces ESPN's all-time/weekly). Player sections LEAD (this
        league's nature), team sections follow with a contributors detail;
        Score Records on top. Owner column = current owner of the holding
        franchise (MLB-64 caveat).
      * **ACTIVE LENS — the mission** ("real baseball league": if a player
        wasn't started, it didn't happen). ALL records now active-weighted
        (× active_weight), not rostered-total. HR record = Judge's ACTIVE
        62 (2022), not a benched 73. This is the DEFAULT everywhere now
        (Kyle: active-only except where explicitly stated).
      * **AUTO-TRACK non-scored counting stats**: HR/2B/3B set
        auto_tracked=true in stat_classification (H/XBH already were), and
        plumbed HR/doubles/triples through int_cbs__player_game_points +
        int_cbs__player_daily (unpriced display context, like H/AB) so
        they're attributed + active-weightable. ESPN already SCORES these
        so the seed flag is ESPN-neutral (goldens gate it).
      * **HYPERLINK FIX**: Records + team tabs wrote RAW → bref =HYPERLINK
        cells showed as literal text; now USER_ENTERED (like Home) so they
        parse.
      * db.py lowercases result keys — record getters access stat columns
        via stat.lower() (the v2 bug, kept fixed).
- **GOLDEN RE-ANCHOR (Records v2.1 round, 2026-07-13)**: byte-diff
  drifted on 3 ESPN team tabs (CHIN/LAW/SMEL). Diagnosed BEFORE
  re-anchoring: NOT the dim_stat change (verified ESPN-neutral — ESPN
  already scores HR/2B/3B, so the auto_tracked flag is redundant there;
  its catalog was unchanged) and NOT a new extract. It's the float-order
  class from rebuilding the shared TABLE fact fct_player_position_pts:
  four rounding-boundary ppg cells (±0.01: 3.74/3.75, 0.82/0.81,
  2.51/2.52) + one bench-pair tiebreak swap (SMEL Schlittler/Brazoban
  trade adjacent 'Other' rows — same players, no corruption). Same
  documented class as the earlier 382.75/443.05 re-anchors + the
  BRAINTHOUGHTS float-summation-determinism wishlist. Re-anchored the 3
  tabs byte-exact from the fresh render; BBCode + records goldens passed
  untouched. (Byte-diff not re-run: 2h18m warehouse contention this
  cycle; the fixture is a byte-identical copy of the render, and the
  render only READS the built table, so it's deterministic on re-read.)
- [x] **RECORDS v3 — FULL ESPN-MIRROR (Kyle round 7, 2026-07-13):** round-6
      output "still looked almost nothing like the ESPN version"; the data
      layer (season/career, active lens, contributors, abbrev holders) was
      already right — the gap was entirely rendering. Rebuilt
      `build_records_rows` + the data helpers against the pinned ESPN golden
      (`tests/fixtures/almanac_v1_1_0/Records.tsv`):
      * **Powder-blue #f2f7fc header bands** (`_POWDER`) replace the navy;
        scope labels now sit OVER their blocks — "Season" at col B, "All-Time
        Total" at col H (the round-6 bug had them at H/K, over the wrong
        columns).
      * **Negative Records** (ESPN's polar Worst block): Worst Team Total/
        Hitting/Pitching Points, single completed SEASON. Gated so artifacts
        can't own "fewest points": full-length seasons only (season max
        team-total ≥ 60% of the median — auto-drops 2001-2002 coin-flip +
        2020 COVID, no hardcoding), roster-complete team-seasons (≥20 active
        players), closed seasons only (live 2026 excluded via ui_standings).
        Career-worst dropped — "fewest career points" is longevity, not
        futility. Gate self-heals as Track B rebuilds the early era.
      * **Orange recency wash** (`_ORANGE` #fce5cd) on any side whose record
        is held in the live season (31 career leaders still active in 2026).
      * **Player Details stat-lines** (were blank): top-3 marquee counting
        stats, headline first ("62 HR, 177 …" etc.), from `_STAT_LINE_ORDER`.
      * **CATALOG BUG FIXED — 2B/3B were silently missing.**
        `get_cbs_record_catalog` filtered+keyed on `leaderboard_name`, but
        that diverges from stat_name for doubles/triples (2B→DOUBLES,
        3B→TRIPLES) so they never matched `_REC_STAT_COL`. Now keys on
        stat_name (== the union-fact / data-pipeline identity). Catalog went
        18→20; Doubles (Freeman 59, 2023) + Triples (Granderson 22, 2007)
        now render. HR/2B/3B auto_tracked re-confirmed ESPN-NEUTRAL by a
        fresh trace: `get_scored_record_specs` gates on `is_record_candidate
        AND (ESPN-scores-it OR auto_tracked)`, and ESPN already scores all
        three (the pre-change golden lists them), so the flag only newly
        surfaces them in the CBS catalog.
      * Spot-checks all real: Judge 62 HR (2022), A-Rod 154 RBI (2007),
        Bonds 230 BB (2004), Cole 309 K (2019), Reyes 77 SB (2007). Dev
        render clean (19 tabs, exit 0).
- [x] **TRACK B — 2001-2002 backfill worklist HANDED OFF (2026-07-13):**
      season-end roster capture starts 2003, so the walk-back has no anchor
      for 2001-2002; it reconstructs from the transaction log, which covers
      any player who was added/dropped/traded/reserved. The gap is drafted-
      and-held stars who never generated a move. Delivered that gap as a
      fillable seed `dbt_league/seeds/cbs_early_anchors_backfill.csv` (146
      never-transacted producers >100 pts, 66/2001 + 80/2002, tiered
      star>300 / tail, `active_status` pre-set A since never-transacted ⟹
      never-reserved) + legend `CBS_EARLY_ANCHORS_BACKFILL.md` (abbrev ↔ era
      team name; names changed, abbrev is the stable key). The name match
      needed the cbs_name_key FLIP (log stores "Bonds, Barry" vs record book
      "Barry Bonds") — without it the list was a bogus 272/309. Two build-
      side loose ends noted: 2002's vanished Armonk Artillery (TGUN) + an
      unmapped Nightowls franchise bucket. Kyle fills teams manually while
      Track A ran; ingest → synthetic anchors → re-run walk-back is next.
- [ ] **RECORDS v3.1 (Kyle review round 8, 2026-07-13) — partial batch:**
      * **Best/worst by polarity** (his correction): stats route by
        dim_stat.polarity. Positive → main 'best' sections; negative
        (ER, Hits Allowed, Walks Allowed) → Negative Records as 'Most ...'.
        No more negative-polarity stats masquerading as positive records.
      * **Box-score stat order** (`_HIT_ORDER` Hits/2B/3B/HR/XBH/TB/…) not
        alphabetical; **"RBI"** not "RBIs" (CBS-side `_DISPLAY_FIX`).
      * **Owner inherits across re-registrations**: the 16 current franchises
        carry owners; a defunct id that shares an abbrev with a live one
        (FULT 13→30, KD 22→28) inherits it. Multi-owner joins with **" & "**
        (a comma read as Last,First). Confirmed clean split: owned == last
        seen 2025; the 18 blanks all last seen 2003-2022 (genuinely defunct).
      * **ESPN records column widths** applied (A175/B150/C125/F400/G25/L400,
        second panel symmetric).
      * **Formatting-reset fix**: `worksheet.clear()` drops values but NOT
        cell format, so every re-render was layering colours over stale ones.
        Added a full-sheet userEnteredFormat reset as the first style request
        → each render starts clean (Kyle stripped the sheet to diagnose this).
      * STILL OPEN this round (next batch): Arrieta dig verdict (coin-flip
        active-weight deflates the 2001-2002 aces — Randy Johnson 2002's raw
        1142 halves below Arrieta's 1020; the real best season is RJ, not
        Arrieta — a 2001-2002 active-weight fix, not a scoring bug); rate
        stats (ERA/K9/BB9/K:BB computable now, AVG/OBP/SLG/OPS need AB
        plumbed); all-time-only-for-active-teams (career-by-abbrev question);
        unclaimed 2001-2002 players → active on sentinel team.
- [ ] **UNIVERSAL WALK-BACK LAWS (Kyle, 2026-07-14 — codified from the
      Arrieta/Randy Johnson digs; these are GENERAL rules, not per-player
      patches):**
      * **LAW 1 — discipline scopes scoring; slots are irrelevant to the
        bucket.** Every rosterable entity is a hitter or a pitcher (CBS's
        two-way split ids 900/901 make Ohtani two single-discipline
        entities). Hitters cannot occupy pitching slots and pitchers cannot
        occupy hitting slots, so: active hitter → hitting points ONLY;
        active pitcher → pitching points ONLY. A pitcher's batting line and
        a position player's mop-up inning NEVER score. This is ESPN's
        architecture, recycled. Scale of today's violation: every pitcher
        who batted (all NL pitchers 2001-2021) carries phantom hitting
        points — Arrieta 2015 (+29 → false best-total 1020 over Verlander)
        is one instance of a universal engine bug, verified against CBS's
        own feed (no batting line for Arrieta; PLATFORM_POINTS is
        pitching-only; the Ohtani split is the architectural tell).
      * **LAW 2 — the transaction log is a state machine; every event is a
        boundary observation (team, date, from_state, to_state).** Any
        event proves membership on that team that day. A player whose
        FIRST event of a season is a lineup move was on that roster since
        at least the season's earliest recorded transaction (you cannot
        lineup-move a player you don't roster). from_slot classifies the
        state BEFORE the boundary, holding back to the prior boundary or
        the membership start; to_slot classifies the state AFTER, holding
        to the next boundary. Active slots (C/1B/…/OF/DH/U/P/SP/RP) =
        active; BE/RS/IL/DL = inactive. So inactive→active, active→active
        (RJ's P→SP: active the whole way), active→inactive all resolve
        with zero assumptions.
      * **What Law 2 exposed:** int_cbs__roster_stints seeds membership
        from move_type in ('add','trade_in','drop') ONLY — slot/activate/
        reserve events create no stint, so a 2001-2002 draft-and-hold ace
        with only lineup moves (Randy Johnson: one P→SP move, KCM) has NO
        stint, NO anchor (anchors start 2003), and is attributed NOWHERE:
        his 1,142-pt 2002 (the true best season, raw) is absent from the
        active lens entirely. NOT a coin-flip halving — a total drop. The
        elite-pitcher cohort is 99-100% logged (verb mix: reserve 47 /
        activate 42 / slot 24 vs add 4 across the 2001 aces), so Law 2
        recovers effectively all of them; the zero-event residue (Bonds,
        Sosa) is exactly the Track B manual sheet — the two sets are
        disjoint by construction.
      * **CBS API note (Kyle, 2026-07-14):** the transaction report honours
        `?print_rows=9999` — the ENTIRE season's log in one GET (verified
        2001/all_but_lineup; unconfirmed other seasons + the `all` filter).
        Documented in extract/cbs_ui_capture.py next to the start_row pager
        it supersedes.
- [ ] **LAWS IMPLEMENTED (2026-07-14, commit 7d53029) — rebuild + verify in
      flight:**
      * Law 1: `int_cbs__player_game_points` discipline arbiter — both-group
        (hitting+pitching) player-seasons classify pitcher iff outs >= 3×AB
        (IP >= AB: NL aces land pitcher, mop-up catchers land hitter);
        off-discipline rows DROP, mirroring the crosswalk scope guard that
        already does this for Ohtani's 900/901. Scoped ids bypass the
        arbiter (mlbam-grain dominance would misjudge the halves).
      * Law 2: `int_cbs__roster_stints` + `int_cbs__lineup_intervals` —
        lineup_opening / lineup_evidence membership (see the Laws entry
        above); slot_move state observation forward (to_slot) + backward
        (from_slot, new state_source 'prior_direct'); deterministic
        tie-break vs anchored openings (row_seq 999999 vs 1000000).
      * **BONUS BUG:** stg_cbs__ui_transactions.to_slot NEVER worked — the
        WHEN gate matched neither ilike pattern for 'Moved from X to Y',
        AND the regex used a (?:...) non-capture group Snowflake rejects
        (it errored on the compound-verb rows the gate did pass; unseen
        because nothing consumed to_slot until Law 2). POSIX-safe
        ' to (\\S+)$' now covers all three verb shapes.
      * Verify plan: RJ 2001+2002 attribution rows exist, full-weight
        active, pitching-only; Arrieta 2015 1020→991 (Verlander 2019
        becomes best total AND best pitching); stint counts by
        open_channel; ESPN goldens (float-order re-anchor class may recur
        on the shared-fact rebuild); CBS dev re-render.
- [ ] **ACTIVE-WEIGHTING REVIEW + SEASON-GRAIN ESTIMATOR (Kyle walkthrough,
      2026-07-14):** Kyle audited the era model end-to-end; his mental model
      and the warehouse now agree, with these confirmations + one fix:
      * **Era map (his framing, verified):** 2001-02 log-only (1/0 states
        from the lineup log; never-transacted → the #### sentinel plan,
        still to build); 2003 + 2021-25 anchor+lineup reconstruction, 1/0,
        no estimator; **2004-2020 is the ONLY estimated window** (rates
        exist every year incl. 2004/2006 — his page-walkthrough gaps were
        misreads he confirmed); 2026 captured. Estimator semantics
        verified: production counts only on the player's REAL membership
        days, scaled by Start%/Own% (started-given-owned; start_pct >
        own_pct violations across 9,969 anchor rows: ZERO — Start% is
        out of ALL leagues, as he read it).
      * **Anchor field census (vs his page walkthrough):** 2003 no rates +
        eligibility list; 2004 rates + eligibility; 2005+ rates, no
        eligibility; mlb_status parses 2003+; the page's Pos column is the
        season-end LINEUP SLOT (vocab shift 2020: U + bare P appear) —
        captured as roster_pos, distinct from primary_pos.
      * **2021-25 lineup-log completeness (his challenge):** the lineup
        moves live in the ALL filter (~2,740-5,100 events/season), NOT
        all_but_lineup (his 2-'Moved'-rows view); modern verbs are
        Benched/Activated. **print_rows=9999 CONFIRMED on the all filter**
        (his check) — the capture script can drop start_row batching.
      * **SEASON-GRAIN ESTIMATOR (2e31c08):** Own%/Start% are player-season
        stats, not franchise stats, so the estimator join's franchise
        scoping silently zeroed mid-season stints on teams the player
        didn't finish with — 26,588 days / ~107k pts (4.3%/3.8% of the
        era). Now joins at (season, player) grain; anchor_STATUS stays
        franchise-scoped. **Still dark: ~10.7% of days / 8.4% of
        production** — players on NO year-end anchor that season. Autopsy:
        the head is season-ending injuries to stars (Trout 2019 638,
        Strasburg Shutdown 609, Sale/deGrom/Santana), ~75% of dark
        production in 100+pt stints; a future adjacent-season borrow could
        cover them (Kyle-decision, not taken).
- [ ] **END-OF-DAY BATCH (Kyle greenlit, 2026-07-14):**
      * **Adjacent-anchor borrow** (`estimated_adjacent`, commit cb5e341):
        2004-2020 dark stints borrow the nearest anchored season's ratio
        (prev/next; tie -> higher); scrubs (no anchor anywhere) stay dark.
      * **Sentinel #### team** (fid 9999, cb5e341): 2001-2002 zero-event
        players parked assume-active so they surface in PLAYER records;
        fenced from TEAM records (team_season filter) + team pages (not
        active); retired by the backfill. cbs_franchises seed row added.
      * **Team-page styling**: navy scope headers -> powder-blue (matches
        Records + ESPN). Bench already capped 8/10; years-of-service
        already present on the all-time board.
      * **TEAM-PAGE SEMANTICS — RESOLVED (Kyle, 2026-07-14; full spec in the
        project_cbs_team_pages memory + build_team_tab docstring):** BOTH
        sides are THIS franchise's OWN best lineup (current-season vs
        all-time cumulative), NOT the all-league team. Starters = optimal by
        ACTIVE points for this team; bench + others = TOTAL points on
        roster. The Tm cols (A & P) = where the player is rostered NOW
        (`*` this team / abbrev another / blank unclaimed) -- which is why
        CAL's all-time page shows Freeman with Tm='BP' (CAL's best-ever 1B,
        now on BP). Players recur across pages by design.
      * **TEAM-PAGE 1:1 STATUS:** structurally the ESPN shape (two-scope
        Best Lineup + bench + years-of-service, powder-blue headers) but not
        yet column-exact. Remaining: the `Tm` + MLB-`Team` columns;
        slash-line columns (Avg/OBP/Slg/HR/SB, W-L-Sv/ERA/WHIP/K/BB -- need
        AB/H/ER/outs plumbed into the lineup selection like the Records rate
        stats); the `Bench/IL Points` column; the capped "Others" overflow.
- [ ] **RECORDS POLISH (Kyle round 9, 2026-07-14):**
      * **Rate stats via the REUSED ESPN helpers** (Kyle: no separate CBS
        method): the shared fact already carries every component
        (h/ab/hbp/sf/tb/er/outs/p_h/p_bb — the crosswalk + MLB-72 union put
        CBS's numbers in ESPN's columns), so we pass them straight through
        `_hitting_rate`/`_pitching_rate`. Added AVG/OBP/SLG/OPS (hitting) +
        ERA/WHIP/K9/BB9/K:BB (pitching) to the player + team sections. Only
        plumbing was adding AB/HBP/SF/L to the CBS aggregation (never needed
        before). Verified real: Bonds .609 OBP / 1.422 OPS (2004), Kershaw
        0.72 WHIP / 15.63 K:BB (2016). Interim min-sample qualifiers
        (hitting ≥350 AB season / ≥1500 career; pitching ≥300 / ≥1200 outs);
        **MLB-80** owns the rigorous fantasy-scale thresholds.
      * **Career TEAM records → active franchises only, keyed by ABBREV**
        (item 6.1): re-registrations combine (FULT 13+30 → one 2001-2026
        career); defunct BENT/NYN/VCF gone.
      * **Franchise Hall of Fame** section: top 25 (player × franchise)
        career active-point runs (Lester/HH 2401, Freeman/SED 2278); abbrev-
        keyed, #### excluded, defunct franchises allowed.
      * **Orange recency highlighting removed** (Kyle wasn't sure what it did).
      * **Lineup Slot Records** (LANDED): left = best player-SEASON by active
        pts per slot (C/1B/2B/3B/SS/OF/DH/P), right = the active FRANCHISE
        with the most all-time active pts per slot (abbrev-combined). Built
        from fct_player_position_pts (its `position` column is eligibility-
        derived; 2004-2020 is estimate-only -- caveated with a literal
        asterisk row: "only P and DH reliable"). Verified: C Raleigh 697/25,
        3B A-Rod 786/07 + SS A-Rod 754/01 (dual-position, correct), OF Bonds
        867/01, P Randy Johnson 1112/02, DH Ohtani (Batter). The fancy
        eligibility-spread option (divide points across eligible slots) is
        deferred as Kyle's-call, more-trouble-than-worth for now.
      * **HoF Years of Service**: swapped the flat span for the stint-list
        format (Lester/HH "4: 2009-2010, 2013-2014"). Confirmed no player-
        dedup rule (Freeman/Kershaw recur across franchises at rank 26+).
- [ ] **SESSION HANDOFF (2026-07-14): see `HANDOFF_IDENTITY_DIM_AND_TEAM_PAGES.md`**
      — the comprehensive state + next-steps doc. Gate = **MLB-81** (player
      identity dimension; the middle-initial/alias name bridge: 58 players /
      114k pts / 48% attributed; K-Rod smoking gun; id audit: txn ids
      0%/57%/100% by era, anchors id-less, three id spaces incl.
      ui-only-<mlbam> synthetics). HoS render HELD behind it (lens bug fixed
      in e8f46f3 — daily fact, not position-fact known-state columns; the
      Verlander false-87% lesson). Then: slot-records rework (primary-pos
      dark years, dynamic template, caveat/details placement per Kyle), HoS
      unheld, team-pages 1:1 sprint (CAL.tsv target).
- [x] **MLB-81 LANDED (2026-07-14) — the identity-dim gate.** The walk-back's
      four name-join seams (games↔stints, stints↔lineup, stints↔anchors, anchor
      estimator) + the sentinel now attribute id-first on the mlbam spine
      instead of by fragile `cbs_name_key` equality.
      * **`dim_player_identity`** (new marts/core, PLATFORM-GENERAL per Kyle —
        one map for every future platform, `platform='cbs'` today; a shareable
        cross-platform name/id↔MLBAM artifact in its own right). Grain
        (platform, name_key, season_year); candidates from crosswalk cbs_name +
        mlbam_name + generated variants (middle-initial strip, two-way
        paren-strip) + log-id + captures + the new `player_alias` seed;
        season × MLB-game-presence disambiguates homonyms. Two-way handled by
        data-driven `stat_group_scope` (no Ohtani literal). `player_alias` seed
        = hard renames only (Carmona→433584, Mike/Michael Stanton→519317);
        Kendrick/Morales auto-bridge via mlbam_name.
      * **Rewire**: stints/lineup carry mlbam+scope; single-rostering + lineup
        intervals re-keyed to the mlbam ident (+scope so a two-way player's
        halves stay separate streams — Ohtani 2025 batter fr18 / pitcher fr34).
        Anchor CTEs RE-AGGREGATED to mlbam grain (not decorated) so the LEFT
        JOINs can't silently fan out (the QUALIFY would hide it). S1 =
        `mlbam(+scope) OR (mlbam NULL → name)` = strict superset. Sentinel =
        anti-join of `reconstructed` on the game grain = its exact complement
        (no hand-synced predicate; also dodges a Snowflake decorrelation error).
      * **Canaries (rebuilt fact, 977k rows, all dbt tests green + new
        `assert_cbs_attribution_no_fanout`)**: K-Rod 501→**6,831** attributed
        fpts (6%→~88%, whole Angels peak back — now the single-season Saves
        record, 60 SV 2008); middle-initial class 36.3%→**67.3%** (was an
        outlier, now at the league-wide ~70% norm); Stanton 2010-11 + Ohtani
        2018-24 (unified-entry era) now attribute, halves split; Verlander
        UNCHANGED (control); the ambiguous class still attributes via the name
        fallback (2 Will Smiths, 3 Luis Garcias — the only 18 ambiguous names,
        all genuine homonyms); **attribution_contested 0 / 977,264** (no
        fan-out). Reconciliation vs official standings in-line-to-better
        (2024 1.2% / 2025 1.9% mean abs).
      * **ESPN neutrality**: unit suite 210 pass; almanac byte-diff re-anchored
        exactly ONE cell (CHIN, Palencia ppg 3.75→3.74 — the documented
        float-order class, verified same-player/±0.01/no-CBS-strings); BBCode
        goldens untouched. Pre-existing `test_stat_catalog` staleness surfaced
        (HR/2B/3B auto_tracked committed but the test's expected set wasn't
        updated — unrelated to MLB-81, flagged as a separate task).
      * CBS dev re-rendered for Kyle's eyeball (the merge gate). NEXT: HoS
        render unheld, slot-records rework, team-pages 1:1 (all still queued).
- [x] **MLB-81 follow-up (same day) — the INVERSE middle-initial case (Kyle's
      Miggy catch).** The first cut's middle-initial variant stripped only
      CROSSWALK-side initials (K-Rod: `francisco j rodriguez` → `francisco
      rodriguez`). But the initial can ride the id-less LOG/ANCHOR side instead
      — CBS's 2003-06 roster pages wrote "Miguel M Cabrera" → `miguel m cabrera`
      while the crosswalk carries plain "Miguel Cabrera" — and those forms never
      resolved (mlbam NULL → name-fallback → no match to the plain-named games).
      Miguel Cabrera's 2004-06 breakout (1,874 pts) was dumping into
      "unrostered", topping the Wasted Hall of Shame with a fake 3,453.
      **Fix**: `dim_player_identity` gains an `idless_bridged` candidate set —
      id-less log/anchor name forms that carry a middle initial, bridged to
      mlbam by matching their STRIPPED form to a crosswalk name (the symmetric
      completion of the strip variant). 67 stint-side name-forms now resolve;
      Miggy 2004-06 fully attributes (career 7,288 → **9,410**, his wasted drops
      to ~688 = just his 2020/2022/23 twilight + pre-callup 2003). Ambiguous set
      18 → 23 (genuine homonyms, name-fallback). K-Rod/Verlander unchanged,
      contested still 0. Consumers unchanged (fix is dim-local); stints TABLE
      must rebuild to pick up the new resolution. Post-fix the Wasted HoS top is
      the believable long-career-setup-man class (Joe Smith, Javier Lopez, Bryan
      Shaw, Chad Qualls...), not early-career stars. Reconciliation MAE 2004-06
      ticks +0.5..1.6 — completing attribution WIDENS the known mid-era overshoot
      (reconstructed>official on current-vs-era rules), NOT a misattribution;
      player-level checks clean. ESPN byte-diff re-anchored (4 float-order cells:
      CAL/CHIN/FNA/NPNP ppg + one rounded-sum boundary).
      * **Wasted HoS render (Kyle)**: dropped the HoS **Rank** column — the stray
        column pushed the Breakdown out to unmanaged col L (read as "missing");
        the four HoS columns now land on the Records All-Time shape (Player H /
        Benched Most By I / Wasted J / **Breakdown K**, the wide 400px Details
        column). Breakdown reinstated by construction.
- [x] **RECORDS-PAGE REWORK (Kyle review, 2026-07-14) — render-side, no dbt.**
      * All-time PLAYER record Details -> a franchise team-list ("Albert Pujols
        3,027 H: SED 572, FULT 368, CSC 354, fourteen other teams 1,734"): top-3
        abbrevs + N-spelled-out bucket; a LONE extra team is named, never
        bucketed; the #### sentinel counts as an owner here (fenced only from
        TEAM records) so the split reconciles to the headline.
      * **Lineup Slot Records = OPTIMIZE-LINEUP over player-SEASONS.** Reuses the
        team-page selector (`get_optimal_team_selections`) but keys each season
        as its own asset ('pk|season'), so repeat PLAYERS are fine (A-Rod 3B +
        SS, two seasons) but no season fills two slots -> U is the best REMAINING
        hitter (Ohtani, not an echo of the best OF). Current-roster shape
        (C/1B/2B/3B/SS, OF x3, U, DH, P x9); left = statline detail, right =
        "All-Time Team Totals" contributors; the estimate caveat rides at I77.
      * Negative Records EXCISED (ESPN symmetry). Rate-record Details -> their
        COMPONENTS (AVG "254 hits in 683 AB"; OBP hits/walks/HBP in PA; SLG the
        1B/2B/3B/HR mix; OPS its two parts; pitching analogues).
      * Shared fixes: bref search URLs strip periods ("Francisco J. Rodriguez"
        now resolves; ESPN + CBS); OPS/SLG >= 1.000 format as "1.###" not
        ".1###" (`_dotted_rate` + `_dot`). ESPN byte-diff re-anchored (bref
        period-strip only, no CBS strings); BBCode goldens untouched; unit 210.
- **REQUEST LIST (running)**: team abbrev preferences collect in the
  cbs_franchises seed (MATT, JUNK so far).

### Eligibility grading (derived rule vs CBS's own 2026 captures)

22,996 player-days compared (every captured roster-day with a priced
game). First pass: 76.7% exact-set agreement — and the decomposition
showed the misses were nearly all ONE thing: CBS lists DH only when
it's a player's SOLE position (universal otherwise-unlisted), while we
listed earned-DH alongside other positions (4,011 of the 4,054
over-grant entries were exactly that). Fixed the array to CBS's display
semantics (DH-unless-other-positions). Ohtani canary exact: 900 →
['DH'], 901 → ['P'], matching CBS's cards. Post-fix re-grade: see the
number below (re-run after the DH fix rebuild). The residual
under-grant tail (~1,670 position-days: 1B 447, SS 362, OF 294, 2B 280,
3B 262, C 24 — CBS granted, we didn't) is the primary-estimator +
counting-timing class; SS 43 over-grants the reverse. Small enough that
Best Lineups are barely sensitive; listed as question #6.
**POST-FIX GRADE: 93.26% exact-set agreement** (21,446 / 22,996
player-days); the rest is the under-grant tail above.

### ESPN byte-neutrality (the MLB-72 gate)

Unit suite 210/210. Warehouse goldens: BBCode records/recap EXACT;
almanac byte-diff EXACT on every tab except TWO cells in Advanced
Standings' acquisition block — both VERIFIED dead-center rounding
boundaries (HANG traded-away-active raw = 382.75 exactly; CYCL
traded-away-rostered raw = 443.05 exactly): the mart rounds sums whose
float accumulation order changes on any table rebuild, so those two
cells coin-flip per rebuild — the same verified-ROUND-boundary class
the MLB-57 landing re-anchored. Fixture re-anchored (one line: CYCL);
byte-diff green against it.

### Almanac-v2 questions for Kyle (running list)

6. **Primary-position estimator**: using fielding-argmax (last season,
   else this season) for "primary position", NOT the year-end anchors'
   primary_pos label (name-keyed, year-END timing, ambiguity class) —
   the 2026 grading below measures how close that gets to CBS's actual
   grants. Happy to switch to anchor-primary (or blend) if the grade
   says otherwise.
7. **Estimated-era Best Lineups are weighted**: 2004-2020 lineup slots
   fill by weighted_active_pts = points × start-share estimator
   (est-membership rows with NO estimator contribute 0 — conservative).
   The team-page fidelity label states the era's provenance mix.
8. **Rules-capture oddity** (no action needed for the almanac): the
   2026-07-08 rules payload says add/drops DISABLED + trades NOT
   allowed + weekly lineups — while 1,325 moves happened this season.
   Likely a mid-break freeze snapshot (worth re-capturing at rollover
   so the archived rules read true).
9. **MLB-72's one unmet acceptance line** (Linear not updated from this
   session — comment/flip when you review): "fct_team_season_performance
   grows CBS team-season rows" is deliberately NOT met. That fact builds
   through the weekly-active chain, which is period-keyed — CBS history
   has no periods. CBS team-seasons live in
   mart_team_points_reconciliation (already graded vs official
   standings). If you want them in the shared spine too, the clean route
   is a day-grain branch in fct_team_season_performance parallel to the
   weekly one — a small follow-up ticket if you call for it.


Watch this file for real-time state:
```powershell
Get-Content "C:\Users\kyled\projects\espn-league-manager\.claude\worktrees\modest-montalcini-3af8c4\WALKBACK_PROGRESS.md" -Wait -Tail 30
```
It is committed at every checkpoint, so the branch on GitHub mirrors it.

## Plan (three blocks)

- **A. Membership stints** — identity dictionary (name → CBS id from the
  league's own id-bearing rows), then stint assembly: acquisition edges
  (add/trade_in) + departure edges (drop/trade-out) + the year-end anchor
  closing each season. Players on the anchor with no in-season
  acquisition = the OPENING ROSTER, recovered implicitly (drafts were
  never logged; the anchor-backward design exists exactly for this).
- **B. Active state** — lineup-era activate/reserve/slot intervals within
  stints (2001-2003, 2021+); est_start_share weighting for 2004-2020;
  per-game attribution fact joining the calculated lens, with Kyle's
  per-row provenance flag (captured / reconstructed_day /
  estimated_startshare / year_end_anchor / ambiguous_identity).
- **C. The reconciliation** — reconstructed team season totals vs the
  OFFICIAL final standings points (25 seasons × 16 teams of ground
  truth): the whole reconstruction graded in one mart. Known systematic
  delta: CBS's sparse pre-2023 IRSTR under-counts the platform side.

## Checkpoints

- [x] A1: identity dictionary model (`int_cbs__player_name_ids` + the
      shared `cbs_name_key` macro; 114 ambiguous-name stints flagged —
      the Will Smith / Luis Garcia class)
- [x] A2: roster stints model (`int_cbs__roster_stints`, 20,003 stints
      2001-2025; openings recovered implicitly; trades as paired
      in/out edges)
- [x] A3: membership self-audits — **100% ANCHOR COVERAGE, all 23
      anchored seasons** (every one of the 10,449 year-end states is
      reproduced by a season-end stint)
- [x] B1: single-rostering truncation (departure-day exclusive; receiver
      owns the effective date) + lineup intervals WITH THE BACKWARD HALF
      (state before a player's first lineup event = the INVERSE of that
      event; zero-event players hold their anchor status)
- [x] B2: `fct_cbs_player_game_attribution` — every priced game
      franchise-attributed with per-row provenance (captured /
      reconstructed_day / estimated_startshare / estimated_membership) +
      state_source + ambiguity/contested/inferred-end flags; one
      attribution per game guaranteed (position-aware tie-break)
- [x] C1: `mart_team_points_reconciliation` — reconstructed vs OFFICIAL
      standings, 25 seasons × 16 teams
- [x] D: COVERAGE EXTENSION — **LANDED. 2003-2019 collapsed from
      80-99% error to 5-13% mean absolute error** (2008: 5.1%, 2003:
      6.6%). 1.2M new gamelog rows; the record book is now era-complete
      (Bonds 73 HR '01 and Randy Johnson 372 K '01 take their thrones;
      Big Unit's 2002 = 1,142 is the all-time fantasy season).
- [x] Ship: schema docs + grain/enum tests for all six walk-back models
      (33/33 green), catalog, Linear, handoff

## Questions / Issues for Kyle (collected as encountered)

1. **The log is lossy on DROPS, in every era** — ~8,000 stints end
   open without a departure record (2,859 players later show up
   acquired elsewhere while their old stint still runs). Verified NOT
   a filter artifact ('all' is a strict superset of 'all_but_lineup',
   87/87 on the dual-source window). Handling: open stints truncate at
   the player's next acquisition elsewhere (a player is on one roster
   at a time), the rest carry `missing_departure` provenance, and the
   Block-C standings reconciliation quantifies the residual. Nothing
   needed from you unless C says the distortion is material.
2. **COVID id discontinuities are a PATTERN**: franchises that sat out
   2020 returned under NEW ids — Foster's Folly 13→30 AND Kimball Drives
   22→28. Both need rows in the MLB-64 continuity-overrides seed
   (worth asking the commissioner if any OTHER 2020 sit-outs exist).
3. (resolved) The `/teams/{id}` link space matches the franchise-id
   space after all — the 2015 'mismatch' was Kimball Drives genuinely
   changing ids across 2020.
4. **(SUPERSEDED 2026-07-13 — my "two systematic residuals" story was
   half wrong, and your skepticism found the truth.)** Your questions
   forced a discipline-split decomposition (hitting vs pitching, each
   vs the UI standings' own split), which revealed:
   - The 2021-22 "overshoot from lossier logs" was mostly **MY BUG**:
     the stint-pairing lead() windowed over acquisitions only (the
     WHERE ran before the window), so NO logged drop ever closed a
     stint — every dropped player's FA-period games kept crediting
     the old team (streamed pitchers = the churn class, which is why
     it looked pitching-heavy). Fixed; the lineup era now grades
     2.4-4.7% mean abs (was 2.4-10.8), and the "lossy on drops"
     census collapsed from ~8,000 flags to **33 true missing
     departures** in 25 years. The log is nearly complete.
   - The residual that SURVIVES the fix: **2021-23 official PITCHING
     runs ~8-11% below reconstructed pitching while hitting tracks
     within ~3-5%, and both disciplines converge in 2024-25.** Our
     credited pitching is flat across years; the OFFICIAL side
     step-jumped +~550/team in 2024. That's the signature of a
     team-level pitching cap (max games/innings) that was removed
     for 2024 — the current rules show `max_total: "No Limit"` on
     every slot, so the knob exists. **QUESTION FOR YOU: did the
     league have a max-games/innings-pitched cap through 2023?**
     If you can confirm (or check the rules page's year switcher,
     like the rosters), we could even model it per-era and collapse
     the 2021-23 deltas.
   - Start-share era stands, with finer texture: roughly unbiased
     2005-2010, undershooting ~8-13% from 2011 on.
5. **The 33 + 18 residual flags decoded (2026-07-13): the dominant
   pattern is MIRROR-PAIR TRADES** — the two lists share names on
   swapped teams with matching dates (Hardy↔Hawpe 7/14/08,
   Kemp↔Bumgarner 8/16/10, Utley↔Morneau 6/9/14, Reed↔K-Rod
   7/28/03...). The 2003 case study shows the log carrying the SAME
   trade in BOTH directions with two effective dates (7/21 + 7/28) +
   double activations — a swap-and-swap-back (rental?) or re-done
   deal. The machine can't know which leg wins by date alone, so it
   flags rather than guesses. DO YOU REMEMBER these — were they
   vetoed/reversed trades, or one-week rentals? Your read decides the
   policy (e.g. void-detection when A→B and B→A legs coexist). The
   remainder are true log silences (Machado '12, Vlad Jr '23 class).
   Full lists: `missing_departure` / `anchor_reopen_needed` flags in
   int_cbs__roster_stints.

## Log

- START. Inputs verified: 52,369 normalized moves / 10,449 anchors /
  25 seasons of finishes.
- A1+A2 built. First audit looked catastrophic (0% coverage) — my
  hand-rolled audit regex was broken, not the model; the dbt-side
  audit (same macro both sides) shows 100% coverage everywhere.
- Missing-departure census: 273 (2001-03) / 4,904 (2004-20) / 2,830
  (2021+). Cross-team overlap: 7,918 stint pairs, 2,859 players →
  Block-B truncation rule.
- CHECKPOINT: Block A committed. Next: B1 truncation + active
  intervals.
- B+C built. FIRST report card looked terrible (-30% to -99% per team)
  and decomposed into TWO separate causes via the rostered-lens column:
  (1) modern era = WEIGHT loss — set-and-forget starters have no lineup
  events, my forward-only intervals defaulted them to reserve; (2)
  pre-2016 = COVERAGE starvation — the universal gamelog layer only
  holds crosswalked players (the 2004+ archive population), so
  early-era rostered players who retired before ~2015 have NO games.
- FIX (modern): the backward state half — prior-inverse intervals +
  anchor-hold. **Modern era now reconciles at 2.4–7.5% mean absolute
  error** (2025: 2.4%, official 8,690 vs reconstructed 8,750). The
  walk-back is VALIDATED where coverage exists.
- NEW CRITICAL PATH (D): extend identity + gamelogs to the UI-history
  population (~thousands of pre-2015 players from the 10,449 anchors +
  moves) via the proven name+season+team machinery, re-run the engine,
  re-measure 2003-2020. This is another extract sweep (detached).
- ISSUE #4 for Kyle: none of this needs you — but note the 2021 mean
  error (7.5%) runs slightly hotter than 2025 (2.4%): older lineup
  logs are a touch noisier (retroactive edits, the truncation class).
  The per-season grades will carry it honestly.
- D IN FLIGHT: the UI-history identity pass matched **2,736 of 2,753
  year-end-roster names (99.4%)** to MLBAM ids (index extended to 2001;
  the 17 stragglers are the rename class — Fausto Carmona, Leo Núñez,
  Melvin Upton — flagged, not guessed). The gamelog extract for the
  extended population (3,852 total ids; ~1,600 genuinely new) is
  RUNNING DETACHED:
  `Get-Content C:\Users\kyled\projects\espn-league-manager\data\mlb_stats\ui_extract_20260712.log -Wait -Tail 5`
  When it lands: mlb_load → rebuild the engine chain → re-measure
  2003-2020 in mart_team_points_reconciliation.
- D PACING FIX: the first hour showed ~10h projected — early-era
  veterans carry 15+ seasons and most calls were PRE-LEAGUE games that
  can never attribute. mlb_stats gained --min-season 2001; sweep
  restarted (idempotent — the hour already landed stays), now fetching
  only league-era seasons per player. Projection ~2-3h.
- D LANDED: 16,732 gamelog season-files swept; 1,203,556 rows loaded
  (MLB_GAMELOGS now 1.8M rows / 3,855 players). Crosswalk staging
  admits the UI population under synthetic 'ui-' ids (only mlbams the
  real crosswalk lacks); full downstream rebuilt 45/45 green.
- **THE FULL-ERA REPORT CARD** (mean absolute error vs official
  standings): 2003-2019 = **5-13%** (was 80-99% before D); 2021-2025 =
  2.4-10.8%; 2001-2002 = ~80% (no roster anchors exist — log-only,
  graded honestly); 2020 = 21% (COVID short season, thin log).
- Phantom-identity check on 2021 (error rose 7.5%→10.8% after D):
  NOT phantoms — the new contributors are Wainwright/Cruz/Posey-class
  stars who retired before 2026, invisible to the FA-only archive, so
  the UI population is their only route in. Legitimate coverage
  unmasked active-state generosity that missing coverage had been
  cancelling out.
- RECORD-BOOK FLOOR FIX: the season floor was the platform archive's
  min (2004) — a pre-UI-history proxy that walled off 2001-2003.
  Now floored by the league's own first season per the UI standings
  (2001). Bonds 73 HR / 867 hitting pts (2001) and Randy Johnson 372 K
  / 1,142 total pts (2002) now lead the book, as they should.
- SHIP: schema docs + tests for all six models (grain uniqueness,
  provenance/state enums, key not_nulls) — 33/33 green.
- **PAIRING FIX (2026-07-13, prompted by Kyle's error-decomposition
  questions)**: `paired`'s lead() now windows over BOTH event kinds
  before the acquisition filter — logged drops/trade_outs actually
  close stints (8,473 + 658 of them; close_type was previously never
  'drop'). Wheeler's "missing departure" was a logged 8/24 drop the
  old code ignored. Report card after: **2003-2019 = 5.1-13.5%,
  2021-25 = 2.4-4.7% mean abs**; anchor audit 99.83% (the 18
  uncovered = real anchor_reopen_needed log gaps, previously masked);
  missing_departure census 33 total. Remaining 2021-23 delta is
  official-side pitching suppression (suspected cap — see issue #4).
- **ROUND 2 OF KYLE'S DECOMPOSITION (2026-07-13): two more fixes.**
  (1) ANCHOR-ARBITRATED TRADE VOIDS — the mirror-pair flags were
  vetoed/reversed swaps the report still renders (2003 Reed/K-Rod: the
  same deal logged BOTH directions under two effective dates). Rule:
  the player's final trade leg is voided when the receiver's anchor
  lacks him and the sender's holds him; genuine rentals never match.
  (2) SUFFIX NORMALIZATION in cbs_name_key — the roster report drops
  Jr/IV where the transaction report keeps it, splitting one player
  into two identities: 2023 Vlad Jr's Meteors half attributed to
  NOBODY. Post-fix his 2023 reads true: Meteors 3/30→7/10
  (opening→trade_out), Kimball Drives 7/10→10/1 (trade_in→anchor). Also
  a phantom-departure guard (a derived trade_out can't synthesize an
  opening when the player demonstrably lived elsewhere earlier).
  **Census now: 22 missing departures + 9 anchor-reopens in 25 years;
  19,992 stints; lineup era grades 4.2/3.9/3.3/2.3/2.1% (2021→2025),
  +1.0 mean signed.** ui- ids renamed ui-only- (Kyle: clearer that the
  prefix marks identity-provenance). IRSTR wobble pattern for Kyle:
  all top 2023-25 diffs are relievers at +2/+3 with OUR side higher —
  initially suspected pass-through double-crediting — REFUTED
  2026-07-13 by CBS's own per-game IRPCT strand rates: Milner 2025
  agrees with our IR/IRS on all 30 inherited-runner games, so CBS's
  SEASON key disagrees with CBS's OWN gamelogs. The wobble is
  internal to their season aggregation; distribution 2023-25 =
  1,110 exact / 81 at +1 / 26 at +2 / 5 at +3 / 4 at −1.
- BONUS — MLB-54's dual-source verify, run NOW instead of at rollover
  (the API snapshot + UI capture already overlap): 2026 swept
  full-season via start_row (1,325 moves / 45 pages), API log
  full-outer-joined vs UI on (franchise, player id, move type,
  effective date) → **746/748 exact, zero UI-only**. The 2 misses are
  one pre-season trade the UI report structurally omits (Torkelson +
  Early, 3/25) — the walk-back's opening-recovery already absorbs that
  class. UI pipeline verified end-to-end; MLB-54 and MLB-63 both
  flipped Done in Linear with full result comments.

## Round — Team pages 1:1 (2026-07-16, overnight)

- **CBS TEAM TABS = ESPN SHAPE, LANDED.** Kyle's call: nothing from the
  old CBS two-band tab worth preserving; the tide flows ESPN->CBS with
  Years of Service the one counter-current (added to ESPN earlier in the
  session). Architecture is REUSE, not imitation: a new provider
  `get_cbs_team_history_data` emits ESPN's exact row contract (both
  scopes) from `fct_player_daily_performance` (active-weighted points +
  stat tail, bench_il = total*(1-weight), roster-tenure days via
  get_roster_days, LISTAGG service seasons, current-roster Tm column
  with the by-name id-split fallback); `_cbs_optimal_team` adapts
  get_best_lineup as the starters selector; the SHARED
  `almanac_logic.build_team_history_tabs` renders both leagues via four
  knobs (optimal_team_fn / title_fn / explain_extra / team_order); the
  ENTIRE format spec extracted to `almanac_render.team_tab_format_specs`
  and consumed by both writers.
- Kyle's product calls (asked before he slept): full-name CBS tab titles
  (the one deliberate asymmetry), "Bench/IL Points" header verbatim (CBS
  RS = its bench; il_days=0 so no IL rows render), Other UNCAPPED (see
  the nutso: 203-1044 rows/tab, Veronicas/Meteors/Hackers >1000),
  provenance sentence APPENDED to the row-3 scoring note (not replacing).
- Other-section filter, BOTH leagues (Kyle): only players with an active
  game OR nonzero points (active or inactive) render — LAW's all-zero
  Agustin Ramirez class is gone (LAW 82->77 rows). Bench/IL pools stay
  unfiltered (IL tenancy is its own story).
- Gotchas found: CBS slot-unknown eras carry lineup_slot='ACT' — leaked
  as "Other - ACT" until the slot list was filtered to SLOT_ORDER
  vocabulary; CBS team tabs must write RAW + reapply '='-cells (mirrors
  the ESPN writer) so zero-padded rates ("040") survive; the writer now
  resizes pre-existing small grids (old 50-row tabs vs 1000+ new rows).
- Retired: build_team_tab, _lineup_block, _lineup_row, _merge_bands,
  _span, _ppg, get_bench_ranking, _TEAM_GLOSSARY, _HITTER_SLOTS.
- Verified: 210 unit tests green; ESPN preview diff vs goldens is
  EXACTLY the expected class (trailing YoS col + header fold + filtered
  Other rows — data rows byte-identical when truncated to 29 cols);
  CBS preview structurally identical to CAL.tsv (pipe headers + Avg/W-L
  sub-headers, W-L adds -Sv only when SV>0, Tm cols, bref links with
  the parenthetical stripped from the search key, Ohtani halves split
  correctly, YoS trailing). Goldens re-anchor still HELD for pre-push.

## Round — Team-tab column slew + Other cap (2026-07-17)

- **Column restructure, both almanacs (shared spec):** Total column
  between Games and Active (= the player's FULL active+inactive for the
  tab's filters -- deliberately undecomposed, so a two-way starter's
  Total is his true total even where Active shows the slot discipline);
  'Bench/IL Points' -> 'Inactive', 'Active Points' -> 'Active'; a merged
  centered size-10 'Points' banner over the trio (G4:I4/W4:Y4);
  'Roster Days' wrapped + vertically merged (E4:E5/U4:U5). All indices
  shifted (pipes K-O/AA-AE, ppg J/Z, YoS idx 31); merges via a shared
  team_tab_merge_ranges(), writers unmerge-then-merge on rerun.
- **Other section = top-100 + honest summary + futility chair (Kyle's
  design):** also-rans capped at 100 by total pts; the cut collapses to
  "N other players under X points, including <next 3>"; the franchise's
  WORST-EVER player (max rostered_days - total_points) is pulled from the
  ranking and pinned as the section's last row, 'Worst - <pos>'. Metric
  chosen after a 3-way shootout: days+games-total only nosed toward
  high-playing-time mediocrity (Kris Bryant class); days-ACTIVE crowned
  hoarded stars (Hader 455 bench pts) which is the Wasted HoS's story;
  days-TOTAL finds the true black holes (Carrasco 137d/-1pt). Veronicas
  1,044 rows -> 141; their cut's teaser includes C.C. Sabathia (the
  100th-best Other sits at 580 pts -- cap is one constant if raised).
- **ppg blanked on Bench/IL/Other/Worst rows** (they rank by TOTAL;
  active-rate there read as noise + never-started players divide by
  zero). Upgrade path if wanted: total per rostered-game needs a small
  SQL add both sides.
- Also this round: row-3 note -> col A3 (both leagues); CBS note now
  era-keyed and sums to 100% ("2026 onward: captured live (2%); ...";
  the old enum sentence dropped estimated_adjacent+sentinel = the
  missing 7%); get_provenance_mix now (season, provenance) grain; tab
  sort fixed to the DISPLAYED title (was abbrev -- looked scrambled);
  width wonk root-caused: auto-resize ran against raw '=HYPERLINK'
  literals before the formula reapply -- reapply now precedes styling;
  bref search keys chop anything after ' (' (fixes Records' Ohtani
  links league-wide).
- **Ohtani roster-days zero (Kyle-spotted):** the split assets' fact
  names ("Shohei Ohtani (Batter)") never matched the transaction log's
  person-grain stint name_key, so get_roster_days returned 0 stint days
  for both halves (games/points were fine -- those flow through the
  id-first attribution seams). Fix: chop the parenthetical before the
  name key in the stint join; person tenure flows to both assets while
  captured-2026 days stay asset-specific (EGG batter 486 = 381 stint +
  105 captured; FULT pitcher 105 -- the two halves live on DIFFERENT
  fantasy teams in 2026, which is extremely CBS).

## Round — Advanced standings, both leagues (2026-07-17, overnight #2)

- **CBS tab renamed 'Standings' -> 'Advanced Standings'** (ESPN parity);
  write_cbs_almanac renames a legacy worksheet IN PLACE (gid kept)
  instead of stranding it -- verified gone on the dev sheet.
- **Finishes matrices (Kyle's list):** 🏆 replaces ① (bold, centered,
  static #57BB8A fill = the scale's rank-1 green); every finish cell
  centered; both matrices carry the Sheets-standard green->yellow->red
  gradient NUMBER-anchored 1 / 8.5 / 16 so a given rank paints the same
  shade in both sections and across renders (per-range MIN/MAX would
  re-scale per section; side effect: 2020's 12-team last place reads
  mid-red, not full red). FORMER FRANCHISES folds into a HIDDEN ROW
  GROUP (header + data rows; the navy band stays visible as the cue,
  +/- expander in the margin).
- **CBS writer grew two format-spec kinds + a stale-state wipe:**
  {'range', 'gradient'} -> addConditionalFormatRule; {'hide_rows':
  (start0, end0)} -> addDimensionGroup + hiddenByUser.
  _stale_style_state_requests deletes prior rules AND row groups before
  restyling (rules STACK per render, re-added groups NEST); the
  metadata read only happens for tabs carrying those spec kinds.
  Rerun-idempotency PROVEN on the dev sheet: full render then targeted
  rerun both read 21 rules + one depth-1 group.
- **CBS POINTS BY LINEUP SLOT (new):** left = 2026 totals by DEPLOYED
  slot (captured era: C/1B/2B/3B/SS/OF/DH/U/P); right = all-time
  per-season averages by PRIMARY POSITION -- pre-2026 lineups carry no
  slot deployments (ACT/RS/EST placeholders) while the daily fact's
  position column is 100% populated in every era (probed: zero nulls,
  8-bucket vocabulary, all 26 seasons). NOTE (round-2 correction): that
  column is the SEASON-level primary/display position from eligibility
  windows, scope-guarded per stat side -- NOT position-of-that-game; a
  2B cameo still credits the primary OF. Closed seasons only (through
  2025) so the in-flight season can't drag the averages. Canonical-
  franchise rows (MLB-64 rollup, numerator AND denominator) in
  current-standings order; ESPN's red->white->green per value column.
- **CBS MLB AFFINITY (new, bottom):** share of each franchise's
  active-lineup games by MLB club -- 2026 left / all-time 2001-2026
  right on one club spine, columns sum to 100% (verified 99.8-100.2
  rounding drift), white->green per block. Substrate =
  fct_cbs_player_game_attribution JOIN int_cbs__player_game_points on
  the engine's game key (the ONLY home of MLB team-of-game; the daily
  fact's pro_team is captured-2026 only). Two-way/pitcher-batting games
  dedupe to one via MAX(weight) across stat-group rows; club names
  label latest-era LEAGUE-WIDE (Expos rows read Nationals -- a
  per-pair MAX_BY would era-split the labels). Fun: FLV all-time StL
  18.4% (verified by independent SQL after a round-2 scare -- see
  below); KCM Royals 16.8% this season.
- **ESPN all-time slot grid:** Table B's twin as per-MATCHUP averages
  (SUM mart_team_slot_production / SUM matchup_periods_played, same
  marts = same regular-season scope); slot union across seasons (IF
  appears from 2025). ESPN 'all-time' = 2025+2026 -- the warehouse's
  full ESPN depth.
- **ESPN Roster Affinity:** same design on the daily fact's per-period
  pro_team snapshot (ESPN abbrev vocabulary, day-accurate across
  trades), active slots only, FA rows excluded; playoff weeks INCLUDED
  (the daily fact carries no playoff flag) -- question below.
- **Two pre-existing ESPN bugs fixed in passing:** (1) conditional-
  format rules accumulated every rerun (nothing ever deleted them) --
  the ESPN writer now wipes the tab's rules before painting, same as
  CBS; (2) the REAL write path never passed acquisition_rows -- the
  MLB-17 blocks existed only in preview TSVs since they shipped; the
  dev sheet now shows them for the first time (expected new content,
  not a regression).
- Builders take the new row sets as OPTIONAL kwargs (sections render
  only when data is supplied), so existing callers/tests held without
  edits; writer table location generalized (_slot_grid_bounds /
  _affinity_bounds; _standings_table_bounds is Table-A-only now).
- Verified: 221 unit tests green (was 211; +CBS standings builder
  suite, +ESPN alltime/affinity/bounds tests); full TSV previews both
  leagues (preview == targeted smoke line-identical); DEV renders both
  leagues clean (quota backoffs absorbed). Goldens/byte-diff untouched
  -- still the deliberately-red pre-push re-anchor. Nothing committed.

### Questions for Kyle (standings round)

1. **All-time slot lens**: the CBS all-time grid pivots on POSITION
   PLAYED because slots don't exist pre-2026. Happy with that, or
   prefer the all-time side stay slot-based and 2026-only until
   captures accumulate?
2. **All-time denominator**: CBS per-season averages exclude in-flight
   2026 (through 2025). Include it instead (a half-season would read
   ~low)? ESPN's per-matchup denominator sidesteps this and includes
   2026.
3. **Finish-gradient anchors**: NUMBER 1/8.5/16 rather than the
   preset's per-range MIN/MAX -- consistent shades across both
   matrices + the trophy fill matches "rank 1" exactly. 2020's 12-team
   last place reads mid-red. OK, or want true per-range auto-scaling?
4. **Former-franchise dupes**: the hidden matrix shows TWO 'Vince
   Coleman Firecrackers' rows (2017-19 + 2021-22) and TWO 'Bent
   Slides' rows -- same-name clubs on distinct canonical ids (the
   COVID-re-id class?). If they're the same franchises, they're
   MLB-64 continuity-override seed candidates.
5. **ESPN affinity scope**: includes playoff weeks (slot grids stay
   regular-season via the mart). Filter playoffs for symmetry, or fine
   for a roster-identity lens?
6. **CBS affinity lens**: ACTIVE-weighted games (estimated era counts
   fractionally by start share). Want a rostered-games variant
   instead/also?
7. **Layer purity**: get_mlb_affinity reads int_cbs__player_game_points
   from the output layer (the only place MLB team-of-game lives).
   Promote a small affinity mart as a follow-up?

### Round 2 (same morning): Kyle's first-look fixes

- **FLV/SLB 18.4% scare RESOLVED -- the grid was right, my handoff
  prose was wrong.** Independent SQL (no builder code): Firefly Lake
  Veronicas = 18.4% Cardinals all-time on 43,390 attributed games (SLB
  is 3.6%). No column-ordering bug; the summary sentence had
  misattributed it to the Browns because the name made a tidier story.
- **Position-semantics correction** (tab note + the bullet above
  fixed): the daily fact's position = season-level PRIMARY position,
  not position-of-game. Feeds Q1, now framed as: (a) primary [current,
  era-complete], (b) per-game via the position fact [2007+ detail],
  (c) slot-only zeroed pre-2026. Kyle torn; alignment with the Records
  page is his deciding axis.
- **Finish gradient -> TRUE auto-scale, per YEAR** (Kyle: 2020's
  12-team last place literally couldn't do worse): one MIN/median/MAX
  rule per season column, ranged over that year's cells in BOTH
  matrices via the writer's new multi-range gradient specs.
- **Affinity restyled (Kyle's list):** red->yellow->green (the
  standard preset trio, same as the finishes), ONE rule spanning both
  blocks -- shared scale so a share paints identically season-side and
  all-time-side (per-block = split the spec back in two); static
  scale-red BASE FILL under the blocks so blank zero-game cells read
  as the 0 they are instead of whiter-than-red; cells now store
  FRACTIONS with a '0.0%' PERCENT number format (12.3% display, one
  decimal universally). Both leagues.
- Kyle's answers logged: Q3 auto-scale DONE; Q4 -> league
  questionnaire (BRAINTHOUGHTS Discussions entry, 2026-07-17); Q5
  playoffs-in-ESPN-affinity fine for now (noted); Q6 active-weighted
  CONFIRMED. Open: Q1 (position lens, torn), Q2 (include in-flight
  season IF logically weighted -- games-volume "effective seasons"
  proposal pending his nod), Q7 (mart home -- leaning fold-into-
  existing; ANSWERED en route: ESPN affinity uses the per-period
  pro_team SNAPSHOT, historic within ESPN's 2025+ window, not
  current-team backfill; CBS is per-game gamelog truth).
- Rule counts after restyle: CBS 43 (25 finish-year + 17 slot + 1
  affinity, one collapsed depth-1 row group) / ESPN 87; both
  write-twice stable on the dev sheets. 221 tests green.

### Round 3 (same morning): standard-season clock + Records alignment

- **Q1 + Q2 RESOLVED by Kyle's own designs.**
- **All-time slot grid re-lensed to the RECORDS convention** (Kyle: "in
  alignment with the records page... 0'd out until capture data
  starts"): the primary-position lens is GONE; both sides now share the
  full deployed-slot vocabulary (C/1B/2B/3B/SS/OF/DH/U/P). The P column
  spans all years -- started pitching IS the P slot in every era, the
  same construction the Records page's Team Totals use -- while hitter
  slots draw only from captured seasons (2026+). Data:
  get_slot_points_alltime (capture rows, season in grain) +
  get_pitching_points_alltime (era-complete active-weighted pitching).
- **Per-season averages -> PACES PER STANDARD SEASON** (Kyle's N-days
  counter-proposal, replacing both my closed-seasons-only rule and the
  games-volume idea he shot down -- games conflate roster behavior +
  league size; a TIME clock doesn't): N = the median CLOSED season's
  count of distinct attributed gameplay days (get_season_gameplay_days);
  each season weighs days/N season-equivalents. 2020 counts the ~third
  it played, a late-draft season self-reports short, and the in-flight
  year now COUNTS (its days so far) -- the through-2025 exclusion is
  gone. Hitter columns pace over capture-era equivalents; P over the
  franchise's full membership equivalents (finishes seasons + current).
  Note text on the tab spells all of this out.
- **ESPN MLB-team direction (Kyle):** NO platform-specific fact/mart.
  The platform-neutral gamelog layer (stg_mlb__player_game already
  carries team-of-game for every game 1984+) is the shared surface both
  leagues' attributions should join when this gets marted -- ESPN wired
  through the same join lands per-game precision inside its 2025+
  window. Logged as the fold target; no render change now (the
  pro_team-snapshot numbers are near-identical within that window).
- Dev re-render: CBS standings tab now 44 rules (25 finish-year + 18
  slot columns + 1 affinity). Sanity on the new right block: hitter
  paces = 2026 totals x N/days-elapsed (~x1.71 mid-July = season ~58%
  run); P paces 3.8-4.2k/season, within a hair of the old position
  lens; DH jumped ~150 -> ~1,000 because it now means the DEPLOYED DH
  slot, not games at the DH position -- the Records-aligned semantic.
  221 tests green (grid test rewritten for the pace split).

### Round 4 (same morning): affinity de-eyesored + the 18.4% receipts

- **Affinity restyle #2 (Kyle: the yellow-mid scale was an eyesore):**
  red -> WHITE -> green on the slot grids' palette (_SCALE_RED/_GREEN,
  now shared constants), one rule PER BLOCK (each matrix scales to its
  own spread -- the shared-scale experiment retired), base fill follows
  the softer red, and each MLB club's biggest devotee bolds per block
  (row-max, ties all bold; CBS builder-side, ESPN writer-side). Rules:
  CBS 45 / ESPN 88, write-twice stable.
- **FLV 18.4% Cardinals: CONFIRMED REAL, third angle.** Per-player
  receipts for franchise 1 x StL: Yadier Molina 687 weighted games
  (2006-22), Carpenter 461, Goldschmidt 454, Edman 316, Arenado 301,
  Wong, DeJong, Nootbaar, Fowler, Piscotty, Craig, J.D. Drew ('01-02).
  FLV is the league's actual Cardinals homer; the Salt Lake Bisons
  just wear the name (3.6% all-time). No column misrender -- the
  original scare came from MY handoff sentence misattributing the
  number to SLB.

### Round 5 (same day): the format batch + two big dawgs

- **Affinity polish (Kyle's list):** section renamed 'MLB Affinity
  Chart', his explainer copy verbatim (nuance flagged in chat: it says
  '2003-2025 estimate starts by start share' -- the start-share
  estimator actually covers 2004-2020, with 2001-03 + 2021-25
  reconstructed from lineup logs), light-gray base for true zero/null
  cells (replacing scale-red), whole-percent display ('0%'), centered.
- **Finishes matrix: Div + Avg columns, titles-then-avg sort.** Div =
  division titles (best league finish within the division that season,
  derived from stg_cbs__ui_standings.division_name now carried through
  get_historic_finishes); Avg = mean finish, 1dp. Both matrices sort by
  (titles DESC, avg ASC). Year columns shifted to E+; the footnote sits
  flush under the former table and defines Div/Avg. Live order: HH
  (5 titles / 7 div / 4.7 avg), FLV (3/4/7.7), KCM (2/5/8.0).
- **BIG DAWG 1 -- the rank chart:** the '<season> STANDINGS -- PERIOD
  N' snapshot table is GONE (adds nothing the CBS site doesn't show).
  In its place: the rank-by-period matrix + an embedded LINE chart fed
  by a HIDDEN helper block (cols AK+, formulas =IF(toggle, 17-rank,
  NA())) with a per-team CHECKBOX row -- spaghetti control. The 17-flip
  puts 1st at the top because the Sheets API cannot reverse a chart
  axis (axis windowed 0..17, titled 'Position (top = 1st)'; tick
  numbers are the flipped values -- the one wart). Toggles are SHARED
  sheet state (Sheets has no per-viewer series filtering) and a
  re-render resets them all-checked. Writer grew three spec kinds
  (checkboxes -> setDataValidation BOOLEAN, hide_cols, chart ->
  addChart; ChartData needs the sourceRange wrapper -- caught live) and
  the stale wipe now also deletes charts + clears validations +
  unhides columns. Helper VERIFIED live: P1 BWS formula evaluates 6 =
  17-11 vs the matrix.
- **BIG DAWG 2 -- transactional standings (MLB-17's CBS twin):**
  get_acquisition_channels(season) builds season-scoped channels from
  stg_cbs__ui_transactions x the attribution fact -- no stint
  dependency (the stints model stops at 2025; the captured season
  derives straight from the log). Channels: OPENING (no logged
  acquisition = season-start roster; CBS never logged drafts, so
  draft + keeper both live there), FA ADD, TRADE -- a game credits the
  player's latest acquisition by that franchise on/before game date
  (QUALIFY latest-event join). Lost = post-departure production,
  WINDOWED to the player's next re-acquisition by the same franchise
  (drop/re-add/re-drop can't double-count), drop vs trade_out split;
  active lens = other franchises' started points, rostered lens adds
  UNOWNED production (anti-join on attribution). Both lenses render
  ESPN-shaped with per-column polarity gradients (+ _points_gradient_
  low / _diverging_gradient) and 0dp display. CONSERVATION PROOF:
  sum(acquired_active) = 77,912 = the season's exact total active
  points -- every point lands in exactly one channel.
- Dev render: 176 rows, 63 rules (25 finish-year + 18 slot + 18
  acquisition + 2 affinity), 1 chart, write-twice stable (rules,
  group, chart all survive the wipe). 224 tests green (+3: div/avg/
  sort, chart apparatus, acquisition blocks).

### Round 6 (same day): chart to the top + the all-time mirror

- **Chart leads the page** (Kyle: 'should be at the top'): navy band ->
  toggles -> chart area (rows ~7-25, helper hidden at AK+) -> the rank
  matrix BELOW it. Helper formulas now reference downward; a layout
  assert guards the arithmetic that places the matrix header.
- **ALL / NONE master toggles** appended after the team boxes: native
  checkboxes can't write each other (that needs Apps Script), so the
  masters OVERRIDE -- plotted = IF(ALL, on, IF(NONE, off, own box)).
  One click to plot everything, one to mute everything, both off =
  individual control; individual states survive a master flip.
- **All-time acquisition mirror (Kyle's ask, active franchises via the
  usual canonical filter):** get_acquisition_channels_alltime reads
  int_cbs__roster_stints for the historic channels -- the engine
  already resolved the log's warts, so open_channel maps add/trade_in
  and everything else (opening / lineup_opening / lineup_evidence) is
  OPENING-class; a game credits the stint holding its date. Lost stays
  SEASON-BOUNDED (departure window = next same-franchise stint that
  season, else Dec 31) -- decades of a dropped prospect never count.
  The builder sums these with the season query for 'All-Time Active /
  Rostered Lens (2001-2026)' blocks under the season pair.
  CONSERVATION EXACT: channeled 3,079,134 = attribution total
  3,169,369 minus the 2001-02 sentinel's 90,235 to the point. (Side
  fact: the sentinel DOES carry ~90k active-weighted points --
  unattributable by design.)
- **Affinity blurb made era-accurate** (Kyle deferred: 'if you're sure
  you're right'): 2004-2020 = start-share estimates; other years
  reconstruct from lineup logs; 2026 captured. His half-memory ('only
  01 and 02 could be reconstructed') = the NO-ANCHOR years -- 2001-02
  are log-only (no year-end anchors), which is a different distinction
  than estimated-vs-reconstructed.
- Dev render: 244 rows, 81 rules (+18 for the all-time blocks), chart
  on top verified live (toggles rows 5-6, matrix at 26). Write-twice
  stable. 225 tests green.

### Round 7 (same day): Kyle's mockup pass + the trade-lost bug

- **KYLE FOUND A REAL BUG from the sheet**: every Trade-lost cell read
  0 because stg_cbs__ui_transactions carries NO trade_out rows -- the
  UI report logs trades ONE-SIDED (the receiver's trade_in; the stint
  engine derives out-edges itself, which is why the all-time side was
  fine). Fix: the season query synthesizes the sender's trade_out from
  counterparty_franchise_id. Season traded-away active: 0 -> 550.
- **Acquisition tables rebuilt to Kyle's on-sheet mockup:** ONE table
  per lens, season half left / all-time half right on the ACTIVE-
  canonical-franchise spine (formers gone -- his 1:1 mirroring ask),
  ranked by the season half's Total. Terse headers under merged group
  bands ('Points Acquired Via' / 'Points Lost Via' / 'Net Points via'),
  Pickup ahead of Trade. ESPN mirrored: ACQUISITION_HEADER renamed
  (Keeper/Draft/Pickup/Trade/Total | Release/Trade/Total | FA/Trade) +
  ACQUISITION_BAND_ROW above each block, format_acquisition_row
  reordered (fa before trade), gradient positions unchanged by luck of
  polarity classes. CBS writer now applies builder merges on non-team
  tabs (unmerge-first).
- **Rank matrix DELETED** (the chart covers it): the helper block is
  now self-contained -- hidden cols AK+ carry Period, the plot
  formulas, AND the raw ranks as plain values the formulas read.
- **Toggle scheme per Kyle:** literal uncheck-others needs Apps Script,
  so his fallback shipped -- defaults are individual boxes OFF + one
  ALL master ON (plotted = OR(ALL, own)). Uncheck ALL, check a team,
  see one line. NONE button dropped as redundant.
- **Finishes matrix moved up** into the matrix's old slot (right under
  the chart) and grew: the in-flight season as the LAST column (plain
  current rank, no trophy, counts toward nothing), #00ff00 SOLID_MEDIUM
  borders on division-champion cells (48 of them), full-width navy/bold
  past AA (the _section/_header width param -- the old AA default was
  the cutoff Kyle saw), Kyle's explainer verbatim under the band, and
  the footnote now defines Div/Avg + the in-flight rule.
- Dev render: 164 rows, 82 rules, 12 merges, 48 borders, write-twice
  stable both sheets. 226 tests green.

### Round 8 (same day): the ESPN rank chart (CBS mirrored back)

- **CBS declared DONE by Kyle**; ESPN begins with its chart.
- **ESPN has no intra-season standings snapshots -- so the arc is
  RECONSTRUCTED**: get_team_rank_arc computes standings-after-week-N
  from fct_team_weekly_active_performance (cumulative W/T, cumulative
  calculated points as tiebreak, team_id deterministic last resort --
  the almanac's own Table A ordering applied cumulatively; the OFFICIAL
  site's mid-season tiebreakers could order a tied pair differently).
  VERIFIED: the reconstruction's final-week order matches Table A
  exactly, all 14 teams.
- **Builder**: rank_arc_rows optional kwarg; the chart block LEADS the
  tab ('{season} Rank by Week' above Table A), same apparatus as CBS --
  individuals-OFF + ALL-ON toggles, self-contained hidden helper at
  AK+ (Week, plot formulas, raw ranks), n+1 rank flip. Layout without
  the kwarg is unchanged (old tests untouched).
- **Writer**: content-based detection (_rank_chart_bounds keys off the
  '(check to plot)' row + the 'Week' helper header -- the ESPN writer
  computes everything from rows, per its architecture), then
  _rank_chart_requests (checkbox validation, helper-column hide,
  addChart) rides the gradients batch; the wipe now also deletes
  charts, clears validations, and unhides columns. RAW write + a
  formula-reapply pass for the helper '=' cells (the W-L strings still
  need RAW); the tab writer grew CBS-style resize-if-small (the helper
  pushes width to ~col BM).
- Live: LINE chart on the ESPN dev sheet, helper evaluating (wk 1 SMEL
  = rank 1 -> plots 14 on the flipped axis), write-twice stable (88
  rules + chart survive the wipe). 227 tests green. ESPN preview TSVs
  grow again -- same deliberately-red byte-diff bucket, pre-push
  re-anchor unchanged.

### Round 9 (same day, crash-interrupted + resumed): the ESPN mirror

- Machine crashed mid-build; every landed edit survived (the clock
  hoist had applied as a clean move). Resumed and finished.
- **KYLE'S TRUNCATION REPORT WAS A REAL BUG I SHIPPED**: the chart
  helper parked at col AK (36) INSIDE Table A's width (45 for this
  stat set), and hiding helper columns hides them for the whole sheet
  -- Defense/Total/Against vanished. helper_col0 is now dynamic
  (max(45, header width + 5) -> 50 live) and the writer derives the
  column from the helper's 'Week' stamp. Verified live: hidden cols =
  exactly 50-78, Table A tail visible.
- **Finishes-beside-the-chart (his mock, cols V+ on the chart rows):**
  owner names as the spine (spillover, no merges), Titles / all-time
  W% (the Div slot -- ESPN has no division data extracted; noted in
  BRAINTHOUGHTS) / Avg, closed-year columns with 🏆 = the PLAYOFF
  champion (derived: won every playoff week -- consolation brackets
  always carry a loss), in-flight column = the current reconstructed
  rank. Sorted titles then W%. CBS conventions writer-side (navy band,
  trophy fill, per-year auto-gradient, '0.0%' W%). THE DATA EARNS IT:
  Grant Ashford won the 2025 regular season (Avg 1.0) but Keven
  McKendry swept the bracket and wears the trophy.
- **All-time detailed standings, BOTH leagues, stacked below (too wide
  for L/R):** ESPN = Table A's twin from mart_team_season_standings
  summed per team (per-standard-matchup averages over summed
  denominators, ordered by all-time W%; ORDER BY must use the output
  aliases -- SUM(alias) nests aggregates in Snowflake). CBS = its
  FIRST detailed standings, all-time-only (the current season reads
  fine on the CBS site): marquee scored set (_HIT_ORDER/_PIT_ORDER,
  OUTS -> IP) active-weighted, paced per standard season via the
  hoisted clock + _member_equivalents, franchise spine, per-column
  gradients, 0dp.
- **ESPN slot grids -> ONE L/R table** (season totals left / all-time
  per-matchup right, shared spine, slot union) when all-time rows are
  present; legacy single grid otherwise (old tests hold).
- **ESPN acquisition -> CBS's L/R shape:** _ACQ_HALF constants,
  acquisition_half_values (format_acquisition_row retired), gradient
  positions for both halves, sub-labels + band rows, and the ESPN
  writer grew MERGE support (unmerge-first) + band/sub-label
  bold-centering. All-time half = the mart summed across its seasons
  = '2026-' today (2025's topics log isn't cleanly reachable --
  MLB-16); labeled honestly, deepens on its own.
- Dev renders write-twice stable: CBS 184 rows / 102 rules; ESPN 151
  rules with chart + merges + validations surviving the wipe. 229
  tests green.

### Round 10 (same day): affinity re-weighted PA + BF

- **Kyle: games-played underweights pitchers (~5:1).** The affinity
  weight is now INVOLVEMENT = plate appearances + batters faced, both
  almanacs. ESPN: PA = ab+b_bb+hbp+sf, BF = outs+p_h+p_bb+hbp_p,
  straight off the daily fact (get_team_pro_team_games renamed
  get_team_affinity_weights; row keys season_wt/alltime_wt). CBS:
  hitting rows contribute PA (ab+bb+hbp+sf), pitching rows BF
  (outs+ha+bbi -- pitcher-HBP isn't priced at game grain, negligible
  undercount), from the same gamelog join. BONUS: the per-game
  MAX(weight) dedup hack DIED -- a two-way or pitcher-batting game now
  legitimately ADDS its PA and its BF instead of needing collapse.
- Explainers on both tabs now say involvement (PA + BF) and why.
- Sanity: FLV's all-time Cardinals share eased 18.4% -> 16.9% (their
  homer stack is hitter-heavy; pitchers elsewhere now speak). Both dev
  tabs re-rendered, write-twice stable (CBS 102 / ESPN 151 rules).
  229 tests green.

### Round 11 (2026-07-18): records IP + trophy-with-finish + the parity pass

- **CBS Records: pitcher statlines finally show IP.** Kyle's read was
  right -- the Home boards' shared formatter always knew IP; the
  records-side _player_line picks from a fixed marquee list
  (_STAT_LINE_ORDER) that simply LACKED OUTS. Added, displayed as IP
  (/3), magnitude-sorted like its neighbors: 'Best Player Pitching
  Points | 334 K, 260 IP, 30 QS'. The real fix -- ONE unified
  top-N-by-point-contribution statline helper across records/boards/
  both platforms -- is logged in BRAINTHOUGHTS (Wishlist).
- **ESPN finishes: trophy AND finish** ('🏆 2' style) -- in an H2H
  league the champion is the playoff winner, so the regular-season
  finish is real information. Writer trophy detection now matches the
  prefix. BRAINTHOUGHTS wishlist: a champion-definition toggle
  (season-end rankings vs playoff performance).
- **ESPN formatting parity with CBS (Kyle: 'replicate as closely as
  possible, I'll clean up after'):** the NAVY INVERTED -- section
  bands are navy/white (width = the section's own tables, helper rows
  excluded from the measurement), table headers dropped to plain bold;
  explainer rows italic-9 under the acquisition/affinity bands; A1 to
  fontSize 14 with the pale-blue italic subtitle; title+subtitle
  FROZEN (2 rows); every double blank between sections collapsed to
  single (the buffer rows Kyle flagged); slot + acquisition value
  columns display 0dp (Table A keeps 1dp weekly averages
  deliberately); affinity base light-gray '0%' centered (was still
  scale-red '0.0%' from round 7). Mine called out pre-execution: the
  navy flip retints every table on the tab -- intended.
- Renders: CBS Records + both standings tabs, write-twice stable (CBS
  102 / ESPN 151 rules). 229 tests green (layout test re-indexed for
  the single blanks; finishes test asserts '🏆 2').

### Round 12 (2026-07-18): Kyle's ESPN review punch-list + the U divider

- **Trophy de-italicized** in both finishes explainers: textFormatRuns
  (the emoji is 2 UTF-16 units; italics resume at index 2) -- CBS via
  the builder's 'runs' spec, ESPN via an updateCells pass in the
  requests batch.
- **ESPN finishes table hugs the top**: explainer row 1 (beside the
  title), header row 2 (on the frozen subtitle band), teams from 3;
  the navy 'SEASON FINISHES' band went with the move; bounds re-keyed
  off the header ('Team' at V + 'Titles' at Z).
- **Decimal rule, both Table As + the slot grid** (Kyle's repeatable
  rule): per value column, 0 decimals unless the column AVERAGE is
  under 10, then 1 -- CYC keeps its decimal, HR stops wobbling between
  51.9 and 52, Offense/Defense/Total 0dp. Acquisition stays flat 0dp.
- **Slot grid: BOTH halves now Averages per Matchup** (the season side
  divided by matchups played) -- the L/R halves are directly comparable
  for the first time. Sub-labels renamed to match.
- **Section renamed 'Detailed Standings (Weekly Averages[, All-Time])'**
  (Kyle: 'Standings' undersold the per-stat grid).
- **THE U DIVIDER formalized (ESPN)**: every L/R split shares one
  divider column -- U (0-based 20), which is Table A's own
  offense/defense buffer. Left halves pad to T; right halves all start
  at V (slot grid, acquisition incl. band merges at 21+, affinity).
  The affinity season half starts at column C with RIGHT-aligned
  abbrev headers (Kyle's wonky-column fix). CBS left as-is (no sturdy
  equivalent divider -- Kyle's own call).
- **Navy bands unified in width, both tabs**: every band runs as far
  as the widest one (CBS post-pass over the navy specs; ESPN two-pass
  max in the writer).
- **Affinity explainers rewritten to Kyle's copy** ('involvement --
  defined as plate appearances + batters faced... Bold indicates
  highest value for given MLB team'), CBS keeping its provenance
  sentence.
- **Historic-transactions ANSWER (Kyle asked looked-vs-not)**: LOOKED
  AND BLOCKED -- MLB-16's verdict stands: the real log lives in the
  kona_league_communication topics feed (2026 extracts fine); the
  leagueHistory endpoint REJECTS the topics filter, so 2025 isn't
  cleanly reachable by API. Not proven-nonexistent: a site-UI scrape
  (the CBS approach) would be a NEW investigation.
- Renders write-twice stable (CBS 102 / ESPN 154 rules). 229 tests
  green. Kyle: commit + push next once this round eyeballs clean --
  release is close.

### Round 13 (2026-07-18): pre-push touch-ups + the UTIL verdict

- **ESPN affinity spine = full MLB names** (static ESPN_PRO_TEAM_NAMES
  abbrev map, 'Oak' -> 'Athletics'), sorted by name, spilling A:B with
  the season half still at C and all-time at V. Names will CLIP at ~104px
  under the tab's 52px A/B columns -- flagged for Kyle's cleanup pass.
- **Finishes side table re-anchored** (Kyle: 'i got sloppy'): explainer
  row 3, header row 4, teams from 5 -- under the frozen band. Avg now
  INCLUDES the in-flight season's current finish (his call, 'I know
  it's wonky'); Titles stay closed-only.
- **UTIL 'blank' VERDICT: real data, not a casing bug.** The vocabulary
  is 'UTIL' everywhere; the SLOT ceased to exist -- the league's 2026
  settings swapped UTIL for DH (dim_roster_slot_counts: 2025 UTIL
  active/DH zeroed; 2026 DH active/UTIL zeroed) and the daily fact has
  zero 2026 UTIL deployments. The union grid honestly shows the
  settings drift (UTIL all-time-only, DH thin the other way). Nothing
  to standardize.
- **Team-label asymmetry noted for post-push** (BRAINTHOUGHTS): CBS
  full names vs ESPN abbrev+owner = the surviving 1-column offset;
  standardization deliberately deferred by Kyle.
- Renders write-twice stable. 229 tests green.

### Ship (2026-07-18): re-anchor + push (Kyle-authorized)

- Byte-diff goldens re-anchored (REGENERATE_BASELINES=1, the planned
  pre-push step -- Kyle delegated it this time); byte-diff + golden
  BBCode suites green against the new baselines.
- Warehouse sweep surfaced ONE stale pin unrelated to this sprint:
  test_stat_catalog's auto-tracked set lagged the seed's deliberate
  DOUBLES/TRIPLES/HR additions (the CBS records work; the seed rows
  document the intent verbatim). Pin updated to match -- the test only
  runs under -m warehouse, which is how it went unnoticed.
- DH/UTIL record-keeping merge logged in BRAINTHOUGHTS (Kyle: eventual
  slot-class rollup at the dim layer; color scaling suffices for now).
- Committed + pushed to origin/claude/modest-montalcini-3af8c4. Kyle
  picks it up for the next round = the actual release.

### Round 14 (2026-07-18, post-push follow-up): the affinity indent

- Kyle caught the season block still hugging column A (two renders
  running). Landed his mock exactly: spine at C -- riding the Owner
  column's 125px, which is WHY his mock's full club names rendered
  while the column-A version clipped -- spilling into blank D, season
  columns from E, all-time untouched at V, title/explainer at A. The
  writer now derives the spine position from the header row instead of
  assuming column A.
- Fantasy-team columns on the affinity chart sort ALPHABETICALLY by
  abbrev (were standings-ordered; the chart is a find-your-team scan).
  ESPN only for now -- CBS's affinity columns stay standings-ordered
  until Kyle wants the same there.
- Byte-diff golden re-anchored for the new shape; 229 unit + byte-diff
  green. Pushed as 5d10bba.

## Round — Gold-standard headers + Best Individual Seasons (2026-07-17)

- **Header gold standard (Kyle's hand-mocked layout, both leagues):**
  A2 "Optimal Lineups, through [latest data]"; A3 the static
  "Points are calculated according to current season's scoring.";
  the points glossary inline at H1:H3 (Kyle's wording -- Active notes
  the optimizer, Total notes the Bench/"Other" ranking); CBS-only
  "Lineup Data:" block (R1 label right-aligned + era lines at S1:S3,
  merged S:X so the Player column's auto-resize ignores them; when all
  four eras fire, the two log-reconstructed eras merge onto one line).
  Everything in rows 1-3 except A1/A2 renders size-8 italic Dark Gray 4.
  The row-2 pale-blue wash and the Q1:Q3 glossary are gone.
- **Best Individual Seasons by Lineup Slot (both leagues):** a navy A:O
  banner one buffer row under the Current Season readout, then the
  optimal lineup where candidates are PLAYER-SEASONS (position-eligible
  weighted-active points per season for the tab's team). Synthetic
  key|season candidate ids make the shared selector burn each
  player-season once while letting the same player take several slots
  -- Veronicas: Verlander at P 1 (2019) AND P 2 (2011). Bench filled by
  season-total; no Others. Names render "Player (YYYY)". Data: shared
  get_optimal_season_candidates (position fact, season grain) +
  get_team_player_season_stats (ESPN) / the CBS provider's by-season
  scan + get_roster_days_by_season; CBS candidates get the DH/U
  universal-fill synthesis. The block overlays the LEFT 15 columns
  while the all-time side continues alongside; stat sub-header bolding
  now keys per side (row[10] vs row[26]) since the sides no longer
  align below the roster sections.
- Known wrinkle: by-season roster days miss where the stint name_key
  disagrees on middle initials (Miguel Cabrera 2006 = 0d) -- same class
  as the Ohtani parenthetical gap; the real fix is the BRAINTHOUGHTS
  entry moving roster tenure into dbt on id-first seams.
