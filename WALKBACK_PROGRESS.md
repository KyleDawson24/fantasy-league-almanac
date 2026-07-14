# MLB-63 Walk-Back + Almanac v2 — Live Progress Log

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
