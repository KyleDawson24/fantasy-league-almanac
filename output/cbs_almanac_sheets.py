"""output/cbs_almanac_sheets.py

The POINTS-LEAGUE almanac, v2 (Kyle's 2026-07-12 course correction +
2026-07-13 re-sequencing): the CBS workbook MIRRORS the ESPN almanac's
ARCHITECTURE -- nav-first Home with All-League boards, a Records tab, a
Standings tab, and per-franchise team pages whose meat is the Best
Lineup, current season x all-time side by side -- assembled from the
UNIFIED fact family (MLB-72: CBS day-grain rides int_player_daily ->
fct_player_daily_performance -> fct_player_position_pts), never from
CBS-specific assembly.

Tab set:
  Home       navigation table (live #gid links, two-pass write like
             ESPN's), points glossary + provenance notes, All-League
             Team boards: Season-to-Date + All-Time ONLY (no
             Team-of-the-Period -- period boundaries don't exist
             historically).
  Records    PLAYER records only: Best Season (mart_player_season_
             records) side by side with Best Career Totals
             (mart_player_career_records, the MLB-69 axis).
  Standings  the 2026 period-by-period arc + 25 years of season
             finishes (champions marked), active franchises first.
  Team pages one per CURRENTLY-ACTIVE franchise: Best Lineup by
             weighted ACTIVE points per eligible position (slot
             template C/1B/2B/3B/SS/OF*3/DH/U/P*9), current season x
             all-time, bench ranked by total rostered points, with
             the era-fidelity label.

OWNER RE-KEY ISOLATION (MLB-64): every franchise-scoped aggregation
routes its scoping through _entity_where() / the entity_id column
returned by the data getters. When the owner chain-of-custody lands,
re-keying the almanac to OWNERS means changing that one seam (plus tab
naming), not the page builders.

Eligibility semantics (the shared model): a player is a candidate at a
position only where int_cbs__eligibility_windows opened a date-scoped
window (CBS's captured rule -- primary + 20 games last year or 10 this
year, after-achievement). The DH and U SLOTS are universal-fill
(CBS: "Everyone is eligible at DH"; U is the utility slot) --
implemented by synthesizing DH/U candidacies from each player's hitting
production, mirroring how CBS's own lineup page offers those slots.

Points lens: weighted_active_pts -- identical to binary active points
wherever the day's state is KNOWN (2026 captures; 2001-03 + 2021+
reconstruction), the start-share estimator on 2004-2020, zero where
membership is confirmed but activity unknown. The provenance labels on
every page own that caveat; placement is Kyle's to tweak.

Determinism: no wall-clock timestamps -- every cell is a pure function
of warehouse state, so TSV previews stay golden-able.
"""

import time
from datetime import date

import gspread

import db
from db import league_predicate, query_snowflake
from almanac_data import get_optimal_team_candidates
# Shared board machinery ((a) reuse per Kyle 2026-07-13): the CBS Home
# mirrors the ESPN Home by CALLING its builders, not by imitating them.
# The private imports are deliberate and noted in BRAINTHOUGHTS as the
# (b)-refactor seam -- promoting these to a shared library module.
from almanac_logic import (
    _HOME_SCORING_CALLOUT,
    _deviation_by_slot,
    _merge_home_bands,
    get_optimal_team_selections,
)
from almanac_render import (
    HOME_DEVIATION_LABEL,
    HOME_HEADER,
    format_all_league_team_row_with_deviation,
    home_nav_link,
)
from formatters import fmt_ip, format_top_scorer_stats_line
from sheets_writer import _get_authorized_client


HOME_TAB = 'Home'
RECORDS_TAB = 'Records'
STANDINGS_TAB = 'Standings'

# The league's active-lineup shape, verbatim from the captured rules
# (roster.positions): 19 active = C/1B/2B/3B/SS + OF*3 + DH + U + P*9.
CBS_SLOT_CAPS = {
    'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1,
    'OF': 3, 'DH': 1, 'U': 1, 'P': 9,
}
_HITTER_SLOTS = ('C', '1B', '2B', '3B', 'SS', 'OF', 'DH', 'U')

# Records-tab curation: the scored categories + marquee counting stats.
_RECORDS_POINTS = ['CALCULATED_POINTS', 'CALCULATED_HITTING_PTS',
                   'CALCULATED_PITCHING_PTS']
_RECORDS_HITTING = ['R', 'RBI', 'B_BB', 'SB', 'TB', 'HR', 'H', 'XBH']
_RECORDS_PITCHING = ['K', 'W', 'SV', 'HLD', 'QS', 'CG', 'IRSTR', 'NH']

_NAVY = {'red': 0.12, 'green': 0.20, 'blue': 0.30}
_WHITE = {'red': 1, 'green': 1, 'blue': 1}
_PALE_BLUE = {'red': 0.95, 'green': 0.97, 'blue': 0.99}
_GOLD = {'red': 1.0, 'green': 0.95, 'blue': 0.75}

# Inline twin of the dbt cbs_name_key macro (macros/cbs_name_key.sql) --
# KEEP IN SYNC. Used once, to bridge the stint machine's name_key grain
# to player_key for the RosterDays column.
def _name_key_sql(col):
    return (
        "trim(regexp_replace("
        "trim(regexp_replace(lower("
        "regexp_replace("
        "regexp_replace("
        f"replace({col}, '.', ''),"
        "'^([^,]+,.+?)\\\\s+[A-Z0-9]{1,3}\\\\s+[A-Z]{2,4}$', '\\\\1'"
        "),"
        "'^([^,]+),\\\\s*(.+)$', '\\\\2 \\\\1'"
        ")"
        "), ' +', ' ')),"
        "' (jr|sr|ii|iii|iv)$', ''"
        "))"
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def is_points_league():
    """Format dispatch by data presence: delivered period standings exist
    only for non-H2H leagues (F7). Zero rows -> not a points league."""
    rows = query_snowflake(
        f"SELECT COUNT(*) AS n FROM mart_period_standings"
        f" WHERE {league_predicate()}"
    )
    return rows[0]['n'] > 0


def get_season_context():
    """The almanac's data horizon: active season, latest closed period,
    latest captured roster date, historic era span. All from the data --
    no wall clock."""
    season = query_snowflake(
        f"SELECT MAX(season_year) AS sy FROM mart_period_standings"
        f" WHERE {league_predicate()}"
    )[0]['sy']
    period = query_snowflake(
        f"SELECT MAX(period) AS p FROM mart_period_standings"
        f" WHERE {league_predicate()} AND season_year = {season}"
    )[0]['p']
    roster_date = query_snowflake(
        f"SELECT MAX(roster_date) AS d FROM stg_cbs__rosters"
        f" WHERE {league_predicate()} AND season_year = {season}"
    )[0]['d']
    era = query_snowflake(
        f"SELECT MIN(season_year) AS lo, MAX(season_year) AS hi"
        f" FROM stg_cbs__ui_standings WHERE {league_predicate()}"
    )[0]
    return {'season_year': season, 'latest_period': period,
            'roster_date': roster_date,
            'first_season': era['lo'], 'last_closed_season': era['hi']}


def _entity_where(entity_id, alias=''):
    """THE owner-re-key seam (MLB-64): every franchise-scoped filter in
    this module routes through here. Today the entity is franchise_id
    (= the facts' team_id); the future owner re-key swaps this predicate
    (and the tab labels) without touching the page builders."""
    col = f'{alias}.team_id' if alias else 'team_id'
    return f'{col} = {int(entity_id)}'


def get_standings_arc(season_year):
    """Every period row for the season, standings-ordered within period."""
    return query_snowflake(
        f"SELECT period, team_id, team_name, division_name, standings_rank,"
        f"       points, period_points, rank_change, points_behind_leader,"
        f"       is_latest_period"
        f" FROM mart_period_standings"
        f" WHERE {league_predicate()} AND season_year = {season_year}"
        f" ORDER BY period, standings_rank"
    )


def get_historic_finishes():
    """25 years of season finishes from the parsed UI standings: one row
    per (season, franchise), champions flagged, with per-season names
    (names drift; franchise_id is the spine)."""
    return query_snowflake(
        f"SELECT season_year, franchise_id, team_name, standings_rank,"
        f"       is_champion, total_points, teams_in_season"
        f" FROM stg_cbs__ui_standings"
        f" WHERE {league_predicate()}"
        f" ORDER BY season_year, standings_rank"
    )


def get_active_franchises(roster_date):
    """The currently-active franchises (the 2026 capture is the roster of
    record): [(franchise_id, current_name)] -- ONLY these get team tabs."""
    return query_snowflake(
        f"SELECT DISTINCT team_id, team_name"
        f" FROM stg_cbs__rosters"
        f" WHERE {league_predicate()} AND roster_date = '{roster_date}'"
        f" ORDER BY team_id"
    )


def get_season_records():
    wanted = ", ".join(f"'{s}'" for s in
                       _RECORDS_POINTS + _RECORDS_HITTING + _RECORDS_PITCHING)
    return query_snowflake(
        f"SELECT stat_name, display_name, rank, player_name, season_year,"
        f"       stat_value"
        f" FROM mart_player_season_records"
        f" WHERE {league_predicate()} AND stat_name IN ({wanted})"
        f" ORDER BY stat_name, rank"
    )


def get_career_records():
    wanted = ", ".join(f"'{s}'" for s in
                       _RECORDS_POINTS + _RECORDS_HITTING + _RECORDS_PITCHING)
    return query_snowflake(
        f"SELECT stat_name, display_name, rank, player_name, seasons_played,"
        f"       first_season, last_season, stat_value"
        f" FROM mart_player_career_records"
        f" WHERE {league_predicate()} AND stat_name IN ({wanted})"
        f" ORDER BY stat_name, rank"
    )


def get_provenance_mix(entity_id=None):
    """How the roster states behind the numbers are known, as game-day
    shares per provenance class -- the input to every fidelity label.
    League-wide when entity_id is None, else franchise-scoped."""
    scope = f' AND {_entity_where(entity_id)}' if entity_id is not None else ''
    return query_snowflake(
        f"SELECT provenance, COUNT(*) AS n"
        f" FROM fct_player_daily_performance"
        f" WHERE {league_predicate()} AND provenance IS NOT NULL"
        f"   AND game_date IS NOT NULL{scope}"
        f" GROUP BY provenance"
    )


def _synthesize_universal_slots(candidates):
    """DH and U are universal-fill SLOTS (everyone is DH-eligible; U is
    the utility slot), but the eligibility arrays deliberately carry only
    EARNED positions. Give every player with hitting production a DH and
    a U candidacy at their hitting-position points so the selector can
    fill those slots. Players whose only rows are P (pitcher-scoped
    identities) stay pitchers -- a pitcher never beats a hitter for DH/U
    anyway, and CBS's own cards show them as plain 'P'."""
    out = list(candidates)
    best_hitting = {}
    for c in candidates:
        if c['position'] in ('P',):
            continue
        key = c.get('player_key') or c['player_id']
        cur = best_hitting.get(key)
        if cur is None or (c['position_pts'] or 0) > (cur['position_pts'] or 0):
            best_hitting[key] = c
    have = {(c.get('player_key') or c['player_id'], c['position'])
            for c in candidates}
    for key, base in best_hitting.items():
        for slot in ('DH', 'U'):
            if (key, slot) not in have:
                clone = dict(base)
                clone['position'] = slot
                out.append(clone)
    # Selector contract: sorted by position then points DESC.
    out.sort(key=lambda c: (c['position'], -(c['position_pts'] or 0),
                            str(c.get('player_key') or c['player_id'])))
    return out


def get_best_lineup(entity_id=None, season_year=None,
                    points_type='weighted_active'):
    """The Best Lineup for a scope: candidates from the unified position
    fact -> the shared gap-based selector over the CBS slot template ->
    CBS enrichment. entity_id=None gives the league-wide All-League
    boards; season_year=None gives all-time. points_type
    'weighted_active' is the display lineup; 'rostered' builds the
    alternate lineup behind the Total-Pts Best deviation columns."""
    candidates = get_optimal_team_candidates(
        season_year=season_year,
        team_id=entity_id,
        points_type=points_type,
    )
    candidates = _synthesize_universal_slots(candidates)
    lineup = get_optimal_team_selections(candidates, CBS_SLOT_CAPS)
    _enrich_lineup(lineup, entity_id=entity_id, season_year=season_year)
    return lineup


def _enrich_lineup(lineup, entity_id=None, season_year=None):
    """Merge the CBS stat tail + slash inputs + roster context onto
    selected rows, from the unified daily fact (one query per lineup).
    Weighted games mirror the points lens (estimated days count
    fractionally). The MAX_BY(_, game_date) columns implement the boards'
    current-vs-retired semantics for free: an active player's latest row
    is a captured 2026 day (pro_team / owner filled), a retired player's
    latest row predates the capture era (era-honest NULL -> blank cell).

    period_label='Season' on every row keeps the shared formatter's
    Points cell plain (season-long numbers carry no boxscore link)."""
    keys = [r.get('player_key') for r in lineup if r.get('player_key')]
    for sel in lineup:
        sel['period_label'] = 'Season'
    if not keys:
        return lineup
    quoted = ", ".join("'%s'" % k.replace("'", "''") for k in keys)
    filters = [league_predicate(), f"player_key IN ({quoted})",
               "game_date IS NOT NULL"]
    if season_year is not None:
        filters.append(f"season_year = {season_year}")
    if entity_id is not None:
        filters.append(_entity_where(entity_id))
    rows = query_snowflake(f"""
        SELECT
            player_key,
            MIN(season_year)                          AS first_season,
            MAX(season_year)                          AS last_season,
            SUM(games_played)                         AS games,
            ROUND(SUM(games_played * COALESCE(active_weight, 0)), 1)
                                                      AS weighted_games,
            SUM(r) AS r, SUM(rbi) AS rbi, SUM(b_bb) AS b_bb,
            SUM(sb) AS sb, SUM(tb) AS tb,
            SUM(h) AS h, SUM(ab) AS ab, SUM(hbp) AS hbp, SUM(sf) AS sf,
            SUM(k) AS k, SUM(w) AS w, SUM(l) AS l, SUM(sv) AS sv,
            SUM(hld) AS hld, SUM(qs) AS qs, SUM(outs) AS outs,
            SUM(cg) AS cg, SUM(er) AS er,
            SUM(p_h) AS p_h, SUM(p_bb) AS p_bb,
            SUM(r_pts) AS r_pts, SUM(rbi_pts) AS rbi_pts,
            SUM(b_bb_pts) AS b_bb_pts, SUM(sb_pts) AS sb_pts,
            SUM(tb_pts) AS tb_pts, SUM(k_pts) AS k_pts, SUM(w_pts) AS w_pts,
            SUM(sv_pts) AS sv_pts, SUM(hld_pts) AS hld_pts,
            SUM(qs_pts) AS qs_pts, SUM(outs_pts) AS outs_pts,
            SUM(cg_pts) AS cg_pts, SUM(er_pts) AS er_pts,
            MAX_BY(team_name, game_date)              AS latest_team_name,
            MAX_BY(team_abbrev, game_date)            AS team_abbrev,
            MAX_BY(owner_name, game_date)             AS owner_name,
            MAX_BY(pro_team, game_date)               AS pro_team
        FROM fct_player_daily_performance
        WHERE {' AND '.join(filters)}
        GROUP BY player_key
    """)
    by_key = {r['player_key']: r for r in rows}
    for sel in lineup:
        extra = by_key.get(sel.get('player_key'))
        if extra:
            for k, v in extra.items():
                sel.setdefault(k, v)
        # The two-way pseudo identities display their CBS split name
        # ("Shohei Ohtani (Batter)" -- MLB-68, reported as two players)
        # but the bref search URL wants the human: the shared formatter
        # builds the link from player_name and the text from
        # display_name, so strip the suffix from the former only.
        name = sel.get('player_name') or ''
        if name.endswith(' (Batter)') or name.endswith(' (Pitcher)'):
            sel.setdefault('display_name', name)
            sel['player_name'] = name.rsplit(' (', 1)[0]
    return lineup


def _apply_alltime_board_context(lineup, top_n=3):
    """All-Time board column semantics (Kyle, 2026-07-13): the Fantasy Team
    cell lists every franchise the player earned active points for --
    weighted-active order, capped at top_n; the Owner cell stays blank
    (owner-by-era is MLB-64's chain-of-custody work); the MLB Team cell
    keeps the enrichment's current-or-blank dynamic."""
    keys = [r.get('player_key') for r in lineup if r.get('player_key')]
    if not keys:
        return lineup
    quoted = ", ".join("'%s'" % k.replace("'", "''") for k in keys)
    rows = query_snowflake(f"""
        WITH per_franchise AS (
            SELECT
                player_key,
                MAX_BY(team_abbrev, game_date)                 AS abbrev,
                MAX_BY(team_name, game_date)                   AS name,
                SUM(total_stat_pts * COALESCE(active_weight, 0)) AS pts
            FROM fct_player_daily_performance
            WHERE {league_predicate()} AND game_date IS NOT NULL
              AND player_key IN ({quoted})
            GROUP BY player_key, team_id
            HAVING SUM(total_stat_pts * COALESCE(active_weight, 0)) > 0
        )
        SELECT player_key,
               LISTAGG(COALESCE(abbrev, name), ', ')
                   WITHIN GROUP (ORDER BY pts DESC) AS franchises
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY player_key
                                      ORDER BY pts DESC) AS rn
            FROM per_franchise
        )
        WHERE rn <= {int(top_n)}
        GROUP BY player_key
    """)
    by_key = {r['player_key']: r['franchises'] for r in rows}
    for sel in lineup:
        sel['team_abbrev'] = by_key.get(sel.get('player_key'), '')
        sel['owner_name'] = ''
    return lineup


def get_roster_days(entity_id, season_year=None):
    """Calendar days rostered per player for one franchise: historic stint
    spans (the walk-back's effective intervals, name_key-bridged to
    player identity) + the current season's captured roster dates.
    season_year scopes to one season (the team page's current side);
    None spans the franchise's whole history."""
    name_key_expr = _name_key_sql('p.player_name')
    stint_season = (f" AND season_year = {int(season_year)}"
                    if season_year is not None else '')
    return query_snowflake(f"""
        WITH stint_days AS (
            SELECT name_key,
                   SUM(DATEDIFF('day', stint_start, attribution_end_exclusive))
                       AS days
            FROM int_cbs__roster_stints_effective
            WHERE {league_predicate()} AND franchise_id = {int(entity_id)}
                  {stint_season}
            GROUP BY name_key
        ),
        players AS (
            SELECT DISTINCT p.player_key, p.player_name
            FROM fct_player_daily_performance p
            WHERE {league_predicate('p')} AND {_entity_where(entity_id, 'p')}
              AND p.game_date IS NOT NULL
        ),
        captured AS (
            SELECT player_id AS player_key, COUNT(DISTINCT roster_date) AS days
            FROM stg_cbs__rosters
            WHERE {league_predicate()} AND team_id = '{int(entity_id)}'
                  {stint_season}
            GROUP BY player_id
        )
        SELECT p.player_key,
               COALESCE(s.days, 0) + COALESCE(c.days, 0) AS roster_days
        FROM players p
        LEFT JOIN stint_days s ON s.name_key = {name_key_expr}
        LEFT JOIN captured c ON c.player_key = p.player_key
    """)


def get_bench_ranking(entity_id, season_year=None, exclude_keys=(), top_n=10):
    """Bench section: most TOTAL ROSTERED points for this franchise
    (weight-independent -- everything produced while rostered), for
    players not already in the Best Lineup."""
    filters = [league_predicate(), _entity_where(entity_id),
               "game_date IS NOT NULL"]
    if season_year is not None:
        filters.append(f"season_year = {season_year}")
    if exclude_keys:
        quoted = ", ".join("'%s'" % k.replace("'", "''") for k in exclude_keys)
        filters.append(f"player_key NOT IN ({quoted})")
    return query_snowflake(f"""
        SELECT
            player_key,
            MAX(player_name)                          AS player_name,
            MAX(position)                             AS position,
            MIN(season_year)                          AS first_season,
            MAX(season_year)                          AS last_season,
            SUM(games_played)                         AS games,
            ROUND(SUM(total_stat_pts), 1)             AS rostered_pts,
            ROUND(SUM(total_stat_pts * COALESCE(active_weight, 0)), 1)
                                                      AS weighted_active_pts
        FROM fct_player_daily_performance
        WHERE {' AND '.join(filters)}
        GROUP BY player_key
        HAVING SUM(total_stat_pts) > 0
        ORDER BY rostered_pts DESC, player_key
        LIMIT {int(top_n)}
    """)


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def _pts(value):
    """Integer-style display for point/stat values (326.0 -> '326')."""
    if value is None:
        return ''
    return f"{float(value):,.0f}"


def _num(value):
    if value is None:
        return ''
    return int(value)


def _movement(rank_change):
    if rank_change is None:
        return ''
    rank_change = int(rank_change)
    if rank_change > 0:
        return f"↑{rank_change}"
    if rank_change < 0:
        return f"↓{-rank_change}"
    return '–'


def _safe_sheet_title(title):
    # Mirrors almanac_render._safe_sheet_title (Sheets-invalid characters).
    bad_chars = set('[]:*?/\\')
    cleaned = ''.join('-' if c in bad_chars else c for c in str(title))
    cleaned = cleaned.strip("'").strip()
    return cleaned[:100] or 'Sheet'


def _col(n):
    """1-based column index -> A1 letter(s)."""
    return gspread.utils.rowcol_to_a1(1, n)[:-1]


def _fmt_date(value):
    if value is None:
        return ''
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f'{value:%b} {value.day}, {value.year}'


def _span(row):
    lo, hi = row.get('first_season'), row.get('last_season')
    if not lo:
        return ''
    return str(int(lo)) if lo == hi else f'{int(lo)}–{int(hi)}'


def _ppg(points, games):
    if not games:
        return ''
    return f'{float(points or 0) / float(games):.2f}'


def _merge_bands(left_rows, right_rows, left_width, gap=1):
    """Two-band layout helper: pad the left band to a fixed width, then a
    gap, then the right band. Mirrors the ESPN Home's dashboard shape."""
    merged = []
    for i in range(max(len(left_rows), len(right_rows))):
        left = list(left_rows[i]) if i < len(left_rows) else []
        left += [''] * (left_width - len(left))
        right = list(right_rows[i]) if i < len(right_rows) else []
        merged.append(left + [''] * gap + right)
    return merged


def _provenance_sentence(mix_rows):
    """One-line fidelity label from a provenance mix."""
    total = sum(r['n'] for r in mix_rows) or 1
    share = {r['provenance']: 100.0 * r['n'] / total for r in mix_rows}
    est = share.get('estimated_startshare', 0) + share.get('estimated_membership', 0)
    rec = share.get('reconstructed_day', 0)
    cap = share.get('captured', 0)
    return (f'Lineup states behind these numbers: {cap:.0f}% captured live, '
            f'{rec:.0f}% reconstructed day-by-day from the transaction log, '
            f'{est:.0f}% estimated from year-end start shares (2004–2020 era).')


def _lineup_row(sel, all_time=False):
    """One Best-Lineup row: slot, player, span, days/games, points, ppg +
    the discipline-appropriate stat tail."""
    slot = sel.get('slot_label') or ''
    name = sel.get('display_name') or sel.get('player_name') or '—'
    pts = sel.get('position_pts')
    games = sel.get('weighted_games') or sel.get('games')
    is_pitcher = (sel.get('lineup_slot') == 'P')
    if is_pitcher:
        tail = [_num(sel.get('k')), _num(sel.get('w')), _num(sel.get('sv')),
                _num(sel.get('qs')), fmt_ip(sel.get('outs')) if sel.get('outs') else '']
    else:
        tail = [_num(sel.get('r')), _num(sel.get('rbi')), _num(sel.get('b_bb')),
                _num(sel.get('sb')), _num(sel.get('tb'))]
    row = [slot, name]
    if all_time:
        row.append(_span(sel))
    row += [
        _num(sel.get('roster_days')) if sel.get('roster_days') is not None else '',
        _num(sel.get('games')),
        _pts(pts),
        _ppg(pts, games),
    ]
    return row + tail


def _lineup_block(lineup, all_time=False):
    """Rows for one Best-Lineup side: hitter header, hitter slots, pitcher
    header, pitcher slots. Returns (rows, header_row_indexes)."""
    span_col = ['Yrs'] if all_time else []
    hitter_hdr = (['Slot', 'Player'] + span_col +
                  ['RosterDays', 'Games', 'Active Pts', 'ppg',
                   'R', 'RBI', 'BB', 'SB', 'TB'])
    pitcher_hdr = (['', ''] + ([''] if all_time else []) +
                   ['', '', '', '', 'K', 'W', 'SV', 'QS', 'IP'])
    rows = [hitter_hdr]
    headers = [0]
    hitters = [s for s in lineup if s.get('lineup_slot') in _HITTER_SLOTS]
    pitchers = [s for s in lineup if s.get('lineup_slot') == 'P']
    for sel in hitters:
        rows.append(_lineup_row(sel, all_time=all_time))
    headers.append(len(rows))
    rows.append(pitcher_hdr)
    for sel in pitchers:
        rows.append(_lineup_row(sel, all_time=all_time))
    return rows, headers


# The one wording deviation from ESPN's deviation-column label: CBS
# attribution has no FA lens, so "(incl. bench & FA)" would overclaim.
_CBS_DEVIATION_LABEL = HOME_DEVIATION_LABEL.replace(' & FA', '')

_CBS_GLOSSARY = [
    ('Calculated Points', 'Universal MLB stats priced by the league\'s '
                          'current scoring rules -- verified against CBS\'s '
                          'own awarded totals.'),
    ('Active Points', 'Produced while in the starting lineup (weighted by '
                      'start-share estimates where 2004–2020 daily '
                      'lineups aren\'t recoverable).'),
    ('Rostered Points', 'Everything produced while on the roster, started '
                        'or benched.'),
]


def build_home_rows(context, nav_targets=None):
    """Home as the ESPN two-band dashboard, built by the SHARED board
    machinery: left band (cols A-D) = navigation + team grid + points
    glossary + the provenance sentence; right band (cols F+) = the two
    All-League boards -- Season-to-Date and All-Time, each in ESPN's
    exact column shape (Slot | Team | Player | Fantasy Team | Owner |
    Points | Slash | Stat Line | Total-Pts Best) with the deviation
    columns driven by the rostered-lens alternate lineup.

    CBS exceptions to the ESPN shape, all Kyle-specified (2026-07-13):
    no Team-of-the-Week board (no periods historically); Points cells
    are plain numbers (season-long, no boxscore); the All-Time board's
    Team column is current-MLB-team-or-blank, its Fantasy Team column
    lists the player's top franchises by weighted active points (max 3),
    and its Owner column stays blank until MLB-64 maps owner eras;
    the deviation label drops "& FA" (no FA lens in CBS attribution).

    nav_targets: {tab_title: gid} on the live write -> in-sheet
    =HYPERLINK nav cells; None in previews -> plain text (ESPN pattern).
    """
    season = context['season_year']
    league_name = db.league().display_name
    era = f"{context['first_season']}–{season}"

    # ------------------------------------------------ right band (F..O)
    header = [*HOME_HEADER, _CBS_DEVIATION_LABEL, '']
    season_dev = _deviation_by_slot(context['season_board'],
                                    context['season_board_rostered'])
    alltime_dev = _deviation_by_slot(context['alltime_board'],
                                     context['alltime_board_rostered'])
    right = [
        [f'All-League Team Season-to-Date: {season}'],
        [],
        list(header),
    ]
    right.extend(
        format_all_league_team_row_with_deviation(
            row, season_dev.get(row.get('slot_label')))
        for row in context['season_board']
    )
    right.append([])
    right.append([f'All-League Team: All-Time ({era})'])
    right.append([])
    right.append(list(header))
    right.extend(
        format_all_league_team_row_with_deviation(
            row, alltime_dev.get(row.get('slot_label')))
        for row in context['alltime_board']
    )

    # ------------------------------------------------ left band (A..D)
    left = [['Navigate']]
    left.append([home_nav_link(RECORDS_TAB, RECORDS_TAB, nav_targets),
                 'Best seasons & careers, all-time.'])
    left.append([home_nav_link(STANDINGS_TAB, STANDINGS_TAB, nav_targets),
                 f'{season} race + every finish since '
                 f'{context["first_season"]}.'])
    left.append(['Team Pages', 'Best lineups & benches, current + all-time.'])
    team_titles = context['team_titles']
    for i in range(0, len(team_titles), 2):
        left.append(['', *(home_nav_link(t, t, nav_targets)
                           for t in team_titles[i:i + 2])])
    left.append(['Draft Recap', 'Coming with the draft-history parse.'])
    left.append([])
    left.append(['Points Glossary'])
    left.extend([term, definition] for term, definition in _CBS_GLOSSARY)
    left.append([])
    left.append([_provenance_sentence(context['provenance_mix'])])

    rows = [
        [f'{league_name} Almanac'],
        [_HOME_SCORING_CALLOUT],
        [],
        *_merge_home_bands(left, right, 4, len(header)),
    ]

    # ESPN-restrained styling (mirrors almanac_write._replace_home_tab +
    # _home_label_formats): bold-14 title, pale-blue callout, bold left
    # labels, navy board header rows, 1-decimal points columns.
    last_col = _col(5 + len(header))
    formats = [
        {'range': f'A1:{last_col}1',
         'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': f'A2:{last_col}2',
         'format': {'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.90, 'green': 0.94, 'blue': 0.98}}},
        {'range': 'K:K', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
        {'range': 'O:O', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
    ]
    for i, row in enumerate(rows, 1):
        first = row[0] if row else ''
        right_cell = row[5] if len(row) > 5 else ''
        if first in ('Navigate', 'Points Glossary'):
            formats.append({'range': f'A{i}:D{i}',
                            'format': {'textFormat': {'bold': True}}})
        if isinstance(right_cell, str) and right_cell.startswith('All-League Team'):
            formats.append({'range': f'F{i}:{last_col}{i}',
                            'format': {'textFormat': {'bold': True}}})
        elif right_cell == 'Slot':
            formats.append({'range': f'F{i}:{last_col}{i}',
                            'format': {'textFormat': {'bold': True,
                                                      'foregroundColor': _WHITE},
                                       'backgroundColor': _NAVY}})
    return rows, formats


def build_records_rows(context, season_records, career_records):
    """Records: PLAYER records only -- Best Season x Best Career Totals,
    side by side. Marquee fantasy-point boards top-10; counting stats one
    row per stat (#1 each side)."""
    era = f"{context['first_season']}–{context['season_year']}"
    by_stat_season = {}
    for r in season_records:
        by_stat_season.setdefault(r['stat_name'], []).append(r)
    by_stat_career = {}
    for r in career_records:
        by_stat_career.setdefault(r['stat_name'], []).append(r)

    rows = [
        ['League Records — Players'],
        [f'Calculated lens, {era}. Records count TOTAL production in '
         f'league-era seasons (rostered or not) until the ownership-scoped '
         f'book lands. Two-way players appear as their CBS split identities.'],
        [],
    ]
    formats = [
        {'range': 'A1:L1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': 'A2:L2', 'format': {'textFormat': {'italic': True},
                                      'backgroundColor': _PALE_BLUE}},
    ]

    def _section(label):
        rows.append([label])
        formats.append({'range': f'A{len(rows)}:L{len(rows)}',
                        'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                                   'backgroundColor': _NAVY}})

    def _header(cells):
        rows.append(cells)
        formats.append({'range': f'A{len(rows)}:L{len(rows)}',
                        'format': {'textFormat': {'bold': True}}})

    marquee_label = {
        'CALCULATED_POINTS': 'BEST FANTASY SEASONS & CAREERS — TOTAL POINTS',
        'CALCULATED_HITTING_PTS': 'HITTING POINTS',
        'CALCULATED_PITCHING_PTS': 'PITCHING POINTS',
    }
    marquee_depth = {'CALCULATED_POINTS': 10,
                     'CALCULATED_HITTING_PTS': 3,
                     'CALCULATED_PITCHING_PTS': 3}
    for stat in _RECORDS_POINTS:
        _section(marquee_label[stat])
        _header(['#', 'Best Season', 'Year', 'Points', '',
                 '#', 'Best Career', 'Yrs', 'Seasons', 'Points'])
        depth = marquee_depth[stat]
        seasons = by_stat_season.get(stat, [])[:depth]
        careers = by_stat_career.get(stat, [])[:depth]
        for i in range(max(len(seasons), len(careers))):
            s = seasons[i] if i < len(seasons) else None
            c = careers[i] if i < len(careers) else None
            rows.append([
                _num(s and s['rank']), s and s['player_name'] or '',
                _num(s and s['season_year']), _pts(s and s['stat_value']), '',
                _num(c and c['rank']), c and c['player_name'] or '',
                (f"{_num(c['first_season'])}–{_num(c['last_season'])}"
                 if c else ''),
                _num(c and c['seasons_played']), _pts(c and c['stat_value']),
            ])
        rows.append([])

    def _counting_section(label, stats):
        _section(label)
        _header(['Record', 'Best Season', 'Year', 'Value', '',
                 '', 'Best Career', 'Yrs', 'Seasons', 'Value'])
        for stat in stats:
            s = (by_stat_season.get(stat) or [None])[0]
            c = (by_stat_career.get(stat) or [None])[0]
            label_txt = (s and s['display_name']) or (c and c['display_name']) or stat
            rows.append([
                label_txt,
                s and s['player_name'] or '', _num(s and s['season_year']),
                _pts(s and s['stat_value']), '', '',
                c and c['player_name'] or '',
                (f"{_num(c['first_season'])}–{_num(c['last_season'])}"
                 if c else ''),
                _num(c and c['seasons_played']), _pts(c and c['stat_value']),
            ])
        rows.append([])

    _counting_section('HITTING RECORDS', _RECORDS_HITTING)
    _counting_section('PITCHING RECORDS', _RECORDS_PITCHING)
    return rows, formats


def build_standings_rows(context, arc, finishes, active_franchises):
    """Standings: the current season's period-by-period arc + every
    season finish since the league began, champions marked."""
    season = context['season_year']
    period = context['latest_period']

    rows = [
        ['Standings'],
        [f'{season} through period {period} · finishes back to '
         f'{context["first_season"]} from the league\'s own year-end '
         f'standings pages.'],
        [],
    ]
    formats = [
        {'range': 'A1:AA1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': 'A2:AA2', 'format': {'textFormat': {'italic': True},
                                       'backgroundColor': _PALE_BLUE}},
    ]

    def _section(label, width='AA'):
        rows.append([label])
        formats.append({'range': f'A{len(rows)}:{width}{len(rows)}',
                        'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                                   'backgroundColor': _NAVY}})

    def _header(cells):
        rows.append(cells)
        formats.append({'range': f'A{len(rows)}:AA{len(rows)}',
                        'format': {'textFormat': {'bold': True}}})

    # ---- current standings
    _section(f'{season} STANDINGS — PERIOD {period}')
    latest = [r for r in arc if r['is_latest_period']]
    _header(['Rank', 'Team', 'Points', 'Behind', 'Δ Rank', 'Period Pts'])
    for row in latest:
        rows.append([
            int(row['standings_rank']), row['team_name'],
            _pts(row['points']), _pts(row['points_behind_leader']),
            _movement(row['rank_change']), _pts(row['period_points']),
        ])
    rows.append([])

    # ---- the season arc: rank per period, one row per team
    _section(f'{season} RANK BY PERIOD')
    periods = sorted({int(r['period']) for r in arc})
    _header(['Team'] + [f'P{p}' for p in periods])
    rank_by = {}
    for r in arc:
        rank_by[(r['team_id'], int(r['period']))] = int(r['standings_rank'])
    for row in sorted(latest, key=lambda r: r['standings_rank']):
        rows.append([row['team_name']] +
                    [rank_by.get((row['team_id'], p), '') for p in periods])
    rows.append([])

    # ---- historic finishes matrix
    seasons = sorted({int(r['season_year']) for r in finishes})
    by_franchise = {}
    latest_name = {}
    for r in finishes:
        fid = int(r['franchise_id'])
        by_franchise.setdefault(fid, {})[int(r['season_year'])] = r
        latest_name[fid] = r['team_name']  # season-ordered input
    active_ids = [int(r['team_id']) for r in active_franchises]
    active_name = {int(r['team_id']): r['team_name'] for r in active_franchises}

    _section(f'SEASON FINISHES {seasons[0]}–{seasons[-1]} '
             f'(① = champion; names as of today, franchises tracked by id)')
    _header(['Franchise', 'Titles'] + [str(y) for y in seasons])

    def _finish_cells(fid):
        cells = []
        titles = 0
        for y in seasons:
            entry = by_franchise.get(fid, {}).get(y)
            if entry is None:
                cells.append('')
                continue
            rank = int(entry['standings_rank'])
            if entry['is_champion']:
                titles += 1
                cells.append('①')
            else:
                cells.append(rank)
        return titles, cells

    matrix_start = len(rows)
    for fid in active_ids:
        titles, cells = _finish_cells(fid)
        rows.append([active_name.get(fid, latest_name.get(fid, f'#{fid}')),
                     titles or ''] + cells)
    defunct = sorted(
        (fid for fid in by_franchise if fid not in set(active_ids)),
        key=lambda fid: -max(by_franchise[fid]),
    )
    if defunct:
        rows.append([])
        _section('FORMER FRANCHISES')
        _header(['Franchise', 'Titles'] + [str(y) for y in seasons])
        for fid in defunct:
            titles, cells = _finish_cells(fid)
            rows.append([latest_name.get(fid, f'#{fid}'), titles or ''] + cells)

    # champion highlight: gold background on ① cells
    for i, row in enumerate(rows[matrix_start:], start=matrix_start):
        for j, cell in enumerate(row):
            if cell == '①':
                col = gspread.utils.rowcol_to_a1(i + 1, j + 1)
                formats.append({'range': f'{col}:{col}',
                                'format': {'backgroundColor': _GOLD,
                                           'textFormat': {'bold': True}}})
    rows.append([])
    rows.append(['Season finishes come from the league\'s year-end standings '
                 'pages; 2002 ran 15 teams and 2020 ran 12. Franchise names '
                 'drift across eras — rows are keyed by franchise id, shown '
                 'under their latest name.'])
    formats.append({'range': f'A{len(rows)}:AA{len(rows)}',
                    'format': {'textFormat': {'italic': True, 'fontSize': 9}}})
    return rows, formats


def build_team_tab(context, franchise, current_lineup, alltime_lineup,
                   bench_current, bench_alltime, days_current, days_alltime,
                   provenance_mix):
    """One franchise page: Best Lineup current x all-time side by side,
    bench blocks, fidelity label."""
    season = context['season_year']
    era = f"{context['first_season']}–{season}"
    title = _safe_sheet_title(franchise['team_name'])
    cur_days = {r['player_key']: r['roster_days'] for r in days_current}
    all_days = {r['player_key']: r['roster_days'] for r in days_alltime}
    for sel in current_lineup:
        if sel.get('player_key') in cur_days:
            sel['roster_days'] = cur_days[sel['player_key']]
    for sel in alltime_lineup:
        if sel.get('player_key') in all_days:
            sel['roster_days'] = all_days[sel['player_key']]

    left_rows, left_headers = _lineup_block(current_lineup, all_time=False)
    right_rows, right_headers = _lineup_block(alltime_lineup, all_time=True)
    left_width = len(left_rows[0])

    rows = [
        [franchise['team_name']],
        [f'Best Lineup — {season} season × all-time ({era}), through '
         f'{_fmt_date(context["roster_date"])}. Starting slots take the best '
         f'ACTIVE points at each eligible position (CBS\'s own eligibility '
         f'rule, date-scoped); bench blocks rank TOTAL rostered points.'],
        [_provenance_sentence(provenance_mix)],
        [],
        [f'Current Season — {season}'] + [''] * (left_width - 1) + ['']
        + [f'All-Time ({era})'],
        [],
    ]
    formats = [
        {'range': 'A1:V1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': 'A2:V2', 'format': {'textFormat': {'italic': True},
                                      'backgroundColor': _PALE_BLUE}},
        {'range': 'A3:V3', 'format': {'textFormat': {'italic': True, 'fontSize': 9}}},
        {'range': f'A5:{_col(left_width)}5',
         'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                    'backgroundColor': _NAVY}},
    ]
    right_start_col = left_width + 2
    right_end_col = right_start_col + len(right_rows[0]) - 1
    formats.append({'range': f'{_col(right_start_col)}5:{_col(right_end_col)}5',
                    'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                               'backgroundColor': _NAVY}})

    body_start = len(rows)
    body = _merge_bands(left_rows, right_rows, left_width, gap=1)
    rows.extend(body)
    for h in left_headers:
        r = body_start + h + 1
        formats.append({'range': f'A{r}:{_col(left_width)}{r}',
                        'format': {'textFormat': {'bold': True}}})
    for h in right_headers:
        r = body_start + h + 1
        formats.append({'range': f'{_col(right_start_col)}{r}:{_col(right_end_col)}{r}',
                        'format': {'textFormat': {'bold': True}}})

    rows.append([])

    # ---- bench blocks
    bench_hdr_cur = ['', 'Player', 'Pos', 'Games', 'Rostered Pts', 'Active Pts']
    bench_hdr_all = ['', 'Player', 'Pos', 'Yrs', 'Games', 'Rostered Pts', 'Active Pts']
    bench_left = [[f'Bench — most rostered points, {season}'], bench_hdr_cur]
    for b in bench_current:
        bench_left.append(['', b['player_name'], b.get('position') or '',
                           _num(b['games']), _pts(b['rostered_pts']),
                           _pts(b['weighted_active_pts'])])
    bench_right = [['Bench — most rostered points, all-time'], bench_hdr_all]
    for b in bench_alltime:
        bench_right.append(['', b['player_name'], b.get('position') or '',
                            _span(b), _num(b['games']),
                            _pts(b['rostered_pts']),
                            _pts(b['weighted_active_pts'])])
    bench_start = len(rows)
    rows.extend(_merge_bands(bench_left, bench_right, left_width, gap=1))
    r = bench_start + 1
    formats.append({'range': f'A{r}:V{r}',
                    'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                               'backgroundColor': _NAVY}})
    formats.append({'range': f'A{r + 1}:V{r + 1}',
                    'format': {'textFormat': {'bold': True}}})

    return title, rows, formats


def build_all_tabs(nav_targets=None):
    """Assemble every tab: [(title, rows, formats)], Home first, then
    Records, Standings, and one page per active franchise in current-
    standings order."""
    context = get_season_context()
    season = context['season_year']
    arc = get_standings_arc(season)
    finishes = get_historic_finishes()
    franchises = get_active_franchises(context['roster_date'])
    context['provenance_mix'] = get_provenance_mix()

    # Team tab order = current standings order.
    latest = {r['team_id']: r for r in arc if r['is_latest_period']}
    franchises = sorted(
        franchises,
        key=lambda f: latest.get(f['team_id'], {}).get('standings_rank', 99),
    )

    team_tabs = []
    for fr in franchises:
        fid = int(fr['team_id'])
        current_lineup = get_best_lineup(entity_id=fid, season_year=season)
        alltime_lineup = get_best_lineup(entity_id=fid, season_year=None)
        in_lineups = {s['player_key'] for s in current_lineup + alltime_lineup
                      if s.get('player_key')}
        bench_current = get_bench_ranking(fid, season_year=season,
                                          exclude_keys=in_lineups, top_n=8)
        bench_alltime = get_bench_ranking(fid, season_year=None,
                                          exclude_keys=in_lineups, top_n=10)
        team_tabs.append(build_team_tab(
            context, fr, current_lineup, alltime_lineup,
            bench_current, bench_alltime,
            get_roster_days(fid, season_year=season), get_roster_days(fid),
            get_provenance_mix(fid),
        ))

    context['team_titles'] = [title for title, _, _ in team_tabs]
    # The display lineups (weighted-active lens) + the rostered-lens
    # alternates that drive the Total-Pts Best deviation columns.
    context['season_board'] = get_best_lineup(entity_id=None, season_year=season)
    context['season_board_rostered'] = get_best_lineup(
        entity_id=None, season_year=season, points_type='rostered')
    context['alltime_board'] = _apply_alltime_board_context(
        get_best_lineup(entity_id=None, season_year=None))
    context['alltime_board_rostered'] = get_best_lineup(
        entity_id=None, season_year=None, points_type='rostered')

    home = build_home_rows(context, nav_targets=nav_targets)
    records = build_records_rows(context, get_season_records(),
                                 get_career_records())
    standings = build_standings_rows(context, arc, finishes, franchises)

    return ([(HOME_TAB, *home), (RECORDS_TAB, *records),
             (STANDINGS_TAB, *standings)] + team_tabs)


# ---------------------------------------------------------------------------
# Sheets write
# ---------------------------------------------------------------------------

# The Sheets API caps WRITE REQUESTS PER MINUTE per user. Two defenses,
# both learned from the ESPN writer's history with the same quota:
#   1. One styling batch_update per tab (freeze + cell formats + column
#      widths in a single request) -- ~3 write calls per tab instead of
#      ~6, which keeps a ~19-tab run under the per-minute cap outright.
#   2. When the quota still trips, wait PAST the minute window before
#      retrying (70s, mirroring almanac_write._sheets_call) -- an
#      exponential ladder that tops out under 60s can never outlast a
#      per-minute bucket.

_QUOTA_ATTEMPTS = 3
_QUOTA_WAIT_SECONDS = 70


def _is_quota_error(exc):
    message = str(exc).lower()
    return '[429]' in message or 'quota exceeded' in message or 'rate limit' in message


def _sheets_call(label, fn):
    """Run a Sheets mutation, backing off when the API write quota resets.
    Mirrors almanac_write._sheets_call; extracting the two into a shared
    plumbing module is a follow-up (kept separate so the golden-covered
    ESPN writer stays untouched)."""
    for attempt in range(1, _QUOTA_ATTEMPTS + 1):
        try:
            return fn()
        except gspread.exceptions.APIError as exc:
            if attempt == _QUOTA_ATTEMPTS or not _is_quota_error(exc):
                raise
            print(f"[cbs-almanac] Sheets quota hit during {label}; "
                  f"retrying in {_QUOTA_WAIT_SECONDS}s")
            time.sleep(_QUOTA_WAIT_SECONDS)


def _write_tab(spreadsheet, title, rows, formats, value_input_option='RAW'):
    width = max((len(r) for r in rows if r), default=8)
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {title}',
            lambda t=title, w=width: spreadsheet.add_worksheet(
                title=t, rows=max(len(rows) + 10, 40), cols=max(w, 10),
            ),
        )
    _sheets_call(f'clear {title}', worksheet.clear)
    _sheets_call(
        f'update {title}',
        lambda ws=worksheet, r=rows, vio=value_input_option: ws.update(
            r, 'A1', value_input_option=vio),
    )
    _sheets_call(
        f'style {title}',
        lambda ws=worksheet, t=title, f=formats:
            spreadsheet.batch_update(
                {'requests': _tab_style_requests(ws.id, t, f)}),
    )
    print(f"[cbs-almanac] wrote tab: {title} ({len(rows)} rows)")
    return worksheet


def write_cbs_almanac(sheet_id):
    """Two-pass write, mirroring the ESPN almanac (#25): pass 1 writes
    every non-Home tab so their gids exist; pass 2 renders Home's nav as
    live =HYPERLINK("#gid=...") formulas and writes it last (USER_ENTERED
    so the formulas parse). Idempotent: a rerun overwrites every tab."""
    client = _get_authorized_client()
    spreadsheet = _sheets_call('open', lambda: client.open_by_key(sheet_id))

    tabs = build_all_tabs()
    home = next(t for t in tabs if t[0] == HOME_TAB)
    others = [t for t in tabs if t[0] != HOME_TAB]

    nav_targets = {}
    for title, rows, formats in others:
        ws = _write_tab(spreadsheet, title, rows, formats)
        nav_targets[title] = ws.id

    # Rebuild Home's rows with live nav targets (cheap: row assembly only
    # -- the boards were already computed inside build_all_tabs; rebuild
    # via the same context is avoided by patching nav cells instead).
    home_title, home_rows, home_formats = home
    patched = []
    for row in home_rows:
        patched.append([
            (f'=HYPERLINK("#gid={nav_targets[cell]}&range=A1", "{cell}")'
             if isinstance(cell, str) and cell in nav_targets else cell)
            for cell in row
        ])
    _write_tab(spreadsheet, home_title, patched, home_formats,
               value_input_option='USER_ENTERED')

    # Tab order: Home, Records, Standings, team pages.
    order = [HOME_TAB] + [t for t, _, _ in others]
    by_title = {ws.title: ws for ws in spreadsheet.worksheets()}
    requests = []
    for idx, title in enumerate(order):
        ws = by_title.get(title)
        if ws is not None:
            requests.append({
                'updateSheetProperties': {
                    'properties': {'sheetId': ws.id, 'index': idx},
                    'fields': 'index',
                },
            })
    if requests:
        _sheets_call('sort tabs',
                     lambda: spreadsheet.batch_update({'requests': requests}))


# Mirrors almanac_write._apply_home_tab_dimensions (the ESPN Home): A-D
# left band, E spacer, F-O right band; Slash/Stat Line (L/M) keep the
# default width there and here.
_HOME_WIDTHS = [(0, 1, 100), (1, 2, 125), (2, 3, 100), (3, 4, 50),
                (4, 5, 100), (5, 6, 40), (6, 7, 40), (7, 8, 150),
                (8, 9, 100), (9, 10, 125), (10, 11, 50),
                (13, 14, 150), (14, 15, 50)]
_RECORDS_WIDTHS = [(0, 1, 210), (1, 2, 180), (2, 5, 70), (5, 6, 30),
                   (6, 7, 180), (7, 8, 90), (8, 10, 70)]
_STANDINGS_WIDTHS = [(0, 1, 190), (1, 2, 60)]
_TEAM_WIDTHS = [(0, 1, 55), (1, 2, 170), (2, 7, 62), (7, 12, 46), (12, 13, 30),
                (13, 14, 55), (14, 15, 170), (15, 16, 62), (16, 21, 62),
                (21, 26, 46)]


def _tab_style_requests(sheet_gid, title, formats):
    """Every non-value mutation for one tab as raw batch_update requests:
    frozen header band, the builder's cell formats (converted from the
    gspread batch_format shape), column widths."""
    requests = [{
        'updateSheetProperties': {
            'properties': {
                'sheetId': sheet_gid,
                'gridProperties': {'frozenRowCount': 2},
            },
            'fields': 'gridProperties.frozenRowCount',
        },
    }]
    for spec in formats or ():
        grid_range = gspread.utils.a1_range_to_grid_range(
            spec['range'], sheet_id=sheet_gid)
        fields = ','.join(sorted(spec['format'].keys()))
        requests.append({
            'repeatCell': {
                'range': grid_range,
                'cell': {'userEnteredFormat': spec['format']},
                'fields': f'userEnteredFormat({fields})',
            },
        })
    if title == HOME_TAB:
        widths = _HOME_WIDTHS
    elif title == RECORDS_TAB:
        widths = _RECORDS_WIDTHS
    elif title == STANDINGS_TAB:
        widths = _STANDINGS_WIDTHS
    else:
        widths = _TEAM_WIDTHS
    requests.extend({
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_gid,
                'dimension': 'COLUMNS',
                'startIndex': start,
                'endIndex': end,
            },
            'properties': {'pixelSize': pixels},
            'fields': 'pixelSize',
        },
    } for start, end, pixels in widths)
    return requests
