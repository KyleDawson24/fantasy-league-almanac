"""output/cbs_almanac_sheets.py

The POINTS-LEAGUE almanac (MLB-66 v1): home tab + one tab per fantasy
team, written to the league's own spreadsheet via the registry-resolved
sink (MLB-58). Content assembly for the non-H2H format -- deliberately
NOT a fork of the H2H almanac: no matchups exist, so the spine is the
platform-delivered standings arc (F7), the calculated_-lens record book,
and the captured daily rosters.

Format dispatch is DATA-PRESENCE, never a platform check (the project's
format-modularity rule): a league with rows in mart_period_standings is
a points league (delivered standings only exist where matchups don't),
and generate_almanac_sheet routes here. ESPN's H2H path is untouched.

v1 content (every section states its own data horizon -- the almanac
gets more truthful every season the league keeps using it):
  Home:  current standings + movement, the record book (best seasons,
         calculated_ lens, archive era 2004+), explainers, and a gated
         note for the sections that arrive with the history parse
         (champions 2001-2026, league-shape timeline) and membership
         (roster-scoped records).
  Teams: season standings trajectory, current roster (active/reserve,
         each player's season calculated FPTS).

Plumbing reuse: the OAuth client comes from sheets_writer (same token,
same consent flow); _sheets_call mirrors almanac_write's retry wrapper
(extraction into a shared module is a welcome follow-up -- not done here
to keep the golden-covered ESPN writer untouched).

Determinism: no wall-clock timestamps in any cell -- rows are a pure
function of warehouse state (season, period, roster_date all come from
the data), so TSV previews are golden-able from day one.
"""

import time

import gspread

import db
from db import league_predicate, query_snowflake
from sheets_writer import _get_authorized_client


HOME_TAB = 'Home'

# Record-book curation for the home tab: the scored categories plus the
# marquee counting stats, in display order. (stat_name, ranks shown).
_RECORD_BOOK_POINTS = [
    ('CALCULATED_POINTS', 5),
    ('CALCULATED_HITTING_PTS', 3),
    ('CALCULATED_PITCHING_PTS', 3),
]
_RECORD_BOOK_HITTING = [
    ('HR', 3), ('R', 3), ('RBI', 3), ('TB', 3), ('SB', 3),
    ('B_BB', 3), ('H', 3), ('XBH', 3),
]
_RECORD_BOOK_PITCHING = [
    ('K', 3), ('W', 3), ('SV', 3), ('HLD', 3), ('QS', 3),
    ('CG', 3), ('IRSTR', 3), ('NH', 3),
]

# Active-slot display order on team tabs (CBS deployed-slot codes).
_SLOT_ORDER = ['C', '1B', '2B', '3B', 'SS', 'OF', 'U', 'DH', 'SP', 'RP', 'P']

_NAVY = {'red': 0.12, 'green': 0.20, 'blue': 0.30}
_WHITE = {'red': 1, 'green': 1, 'blue': 1}
_PALE_BLUE = {'red': 0.95, 'green': 0.97, 'blue': 0.99}


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
    latest captured roster date. All from the data -- no wall clock."""
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
    return {'season_year': season, 'latest_period': period,
            'roster_date': roster_date}


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


def get_record_book():
    """The full curated record book, one query."""
    wanted = (_RECORD_BOOK_POINTS + _RECORD_BOOK_HITTING
              + _RECORD_BOOK_PITCHING)
    names = ", ".join(f"'{stat}'" for stat, _ in wanted)
    return query_snowflake(
        f"SELECT stat_name, display_name, rank, player_name, season_year,"
        f"       stat_value"
        f" FROM mart_player_season_records"
        f" WHERE {league_predicate()} AND stat_name IN ({names})"
        f" ORDER BY stat_name, rank"
    )


def get_current_rosters(season_year, roster_date):
    """Latest captured roster per team, each player carrying their
    season-total calculated FPTS (the lens explainer on the tab tells the
    reader what that means)."""
    return query_snowflake(
        f"SELECT r.team_id, r.team_name, r.division_name, r.player_id,"
        f"       r.player_name, r.roster_pos, r.roster_status,"
        f"       r.eligible_positions, r.pro_team, s.stat_value AS fpts"
        f" FROM stg_cbs__rosters r"
        f" LEFT JOIN int_cbs__player_season_stats s"
        f"   ON {league_predicate('s')}"
        f"  AND r.player_id = s.cbs_player_id"
        f"  AND s.season_year = {season_year}"
        f"  AND s.stat_name = 'CALCULATED_POINTS'"
        f" WHERE {league_predicate('r')} AND r.roster_date = '{roster_date}'"
        f" ORDER BY r.team_id, r.roster_status, r.player_name"
    )


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def _pts(value):
    """Integer-style display for point/stat values (326.0 -> '326')."""
    if value is None:
        return ''
    return f"{float(value):,.0f}"


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


def build_home_rows(context, standings_arc, record_rows, team_titles):
    """The home tab: header + explainers, current standings, record book.
    Returns (rows, formats)."""
    season = context['season_year']
    period = context['latest_period']
    league_name = db.league().display_name

    rows = []
    formats = []

    def _section(label):
        rows.append([label])
        formats.append({
            'range': f'A{len(rows)}:G{len(rows)}',
            'format': {
                'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                'backgroundColor': _NAVY,
            },
        })

    def _table_header(cells):
        rows.append(cells)
        formats.append({
            'range': f'A{len(rows)}:G{len(rows)}',
            'format': {'textFormat': {'bold': True}},
        })

    rows.append([f'{league_name.upper()} — LEAGUE ALMANAC'])
    formats.append({
        'range': 'A1:G1',
        'format': {'textFormat': {'bold': True, 'fontSize': 14}},
    })
    rows.append([f'{season} season through period {period} · '
                 f'16-team CBS points league, running since 2001'])
    formats.append({
        'range': 'A2:G2',
        'format': {'textFormat': {'italic': True},
                   'backgroundColor': _PALE_BLUE},
    })
    rows.append([])

    _section('HOW TO READ THIS ALMANAC')
    rows.append(['All fantasy points are the CALCULATED lens: universal MLB '
                 'stats priced by the league’s current scoring rules — '
                 'verified against CBS’s own awarded totals (they match '
                 'exactly wherever CBS tracked the underlying stat).'])
    rows.append(['Records cover the platform archive era (2004–present) and '
                 'count TOTAL production. Roster-scoped records ("while owned") '
                 'arrive with the ownership reconstruction.'])
    rows.append(['Coming as league history lands: champions 2001–2026, the '
                 'league-shape timeline, and franchise histories on the team '
                 'tabs. This almanac gets more truthful every season.'])
    for offset in (2, 1, 0):
        formats.append({
            'range': f'A{len(rows) - offset}:G{len(rows) - offset}',
            'format': {'textFormat': {'italic': True, 'fontSize': 9}},
        })
    rows.append([])

    _section(f'CURRENT STANDINGS — PERIOD {period}')
    latest = [r for r in standings_arc if r['is_latest_period']]
    # Divisions are cosmetic in a points league and this league's feed
    # leaves them unnamed -- the column only appears when the data names
    # any (data-presence conditional, like every format toggle).
    has_divisions = any(r['division_name'] for r in latest)
    header = ['Rank', 'Team']
    if has_divisions:
        header.append('Division')
    header += ['Points', 'Behind', 'Δ Rank', 'Period Pts']
    _table_header(header)
    for row in latest:
        cells = [int(row['standings_rank']), row['team_name']]
        if has_divisions:
            cells.append(row['division_name'] or '')
        cells += [
            _pts(row['points']),
            _pts(row['points_behind_leader']),
            _movement(row['rank_change']),
            _pts(row['period_points']),
        ]
        rows.append(cells)
    rows.append([])

    by_stat = {}
    for record in record_rows:
        by_stat.setdefault(record['stat_name'], []).append(record)

    def _record_section(label, spec):
        _section(label)
        _table_header(['Record', '#', 'Value', 'Player', 'Season'])
        for stat_name, top_n in spec:
            for record in by_stat.get(stat_name, [])[:top_n]:
                rows.append([
                    record['display_name'] if record['rank'] == 1 else '',
                    int(record['rank']),
                    _pts(record['stat_value']),
                    record['player_name'],
                    int(record['season_year']),
                ])

    _record_section('THE RECORD BOOK — BEST FANTASY-POINT SEASONS '
                    '(2004–PRESENT)', _RECORD_BOOK_POINTS)
    rows.append([])
    _record_section('THE RECORD BOOK — HITTING', _RECORD_BOOK_HITTING)
    rows.append([])
    _record_section('THE RECORD BOOK — PITCHING', _RECORD_BOOK_PITCHING)
    rows.append([])

    _section('TEAM TABS')
    rows.append(['One tab per franchise: season trajectory + current roster '
                 'with each player’s season calculated points.'])
    rows.append([' · '.join(team_titles)])

    return rows, formats


def build_team_tab(context, team_standings, team_roster):
    """One team's tab: trajectory + current roster. Returns
    (title, rows, formats)."""
    season = context['season_year']
    period = context['latest_period']
    roster_date = context['roster_date']

    latest = team_standings[-1]
    title = _safe_sheet_title(latest['team_name'])

    rows = []
    formats = []

    def _section(label):
        rows.append([label])
        formats.append({
            'range': f'A{len(rows)}:F{len(rows)}',
            'format': {
                'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                'backgroundColor': _NAVY,
            },
        })

    def _table_header(cells):
        rows.append(cells)
        formats.append({
            'range': f'A{len(rows)}:F{len(rows)}',
            'format': {'textFormat': {'bold': True}},
        })

    rows.append([latest['team_name']])
    formats.append({
        'range': 'A1:F1',
        'format': {'textFormat': {'bold': True, 'fontSize': 14}},
    })
    division = latest['division_name'] or ''
    subtitle_parts = ([f'{division} Division'] if division else []) + [
        str(season),
        f'rank {int(latest["standings_rank"])} of 16 through period {period}',
    ]
    rows.append([' · '.join(subtitle_parts)])
    formats.append({
        'range': 'A2:F2',
        'format': {'textFormat': {'italic': True},
                   'backgroundColor': _PALE_BLUE},
    })
    rows.append([])

    _section('SEASON TRAJECTORY')
    _table_header(['Period', 'Rank', 'Δ', 'Points', 'Period Pts',
                   'Behind Leader'])
    for row in team_standings:
        rows.append([
            int(row['period']),
            int(row['standings_rank']),
            _movement(row['rank_change']),
            _pts(row['points']),
            _pts(row['period_points']),
            _pts(row['points_behind_leader']),
        ])
    rows.append([])

    def _slot_key(player):
        pos = (player['roster_pos'] or '').upper()
        order = (_SLOT_ORDER.index(pos) if pos in _SLOT_ORDER
                 else len(_SLOT_ORDER))
        fpts = player['fpts'] if player['fpts'] is not None else -1
        return (order, -float(fpts), player['player_name'] or '')

    active = sorted((p for p in team_roster if p['roster_status'] == 'A'),
                    key=_slot_key)
    reserve = sorted((p for p in team_roster if p['roster_status'] != 'A'),
                     key=_slot_key)

    def _roster_section(label, players):
        _section(label)
        _table_header(['Slot', 'Player', 'MLB', 'Eligible',
                       f'{season} FPTS', ''])
        for player in players:
            rows.append([
                player['roster_pos'] or '',
                player['player_name'],
                player['pro_team'] or '',
                player['eligible_positions'] or '',
                _pts(player['fpts']),
                '',
            ])

    _roster_section(f'CURRENT ROSTER — ACTIVE (as of {roster_date})',
                    active)
    rows.append([])
    _roster_section('CURRENT ROSTER — RESERVE', reserve)
    rows.append([])
    rows.append(['FPTS = season-total calculated points (all MLB production '
                 'under league scoring, ownership-blind until the membership '
                 'reconstruction lands).'])
    formats.append({
        'range': f'A{len(rows)}:F{len(rows)}',
        'format': {'textFormat': {'italic': True, 'fontSize': 9}},
    })

    return title, rows, formats


def build_all_tabs():
    """Assemble every tab: [(title, rows, formats)], Home first, team tabs
    in current-standings order."""
    context = get_season_context()
    season = context['season_year']
    arc = get_standings_arc(season)
    rosters = get_current_rosters(season, context['roster_date'])

    arc_by_team = {}
    for row in arc:
        arc_by_team.setdefault(row['team_id'], []).append(row)
    roster_by_team = {}
    for row in rosters:
        roster_by_team.setdefault(row['team_id'], []).append(row)

    # Team tab order = current standings order.
    latest = sorted((r for r in arc if r['is_latest_period']),
                    key=lambda r: r['standings_rank'])

    team_tabs = []
    for standing in latest:
        team_id = standing['team_id']
        team_tabs.append(build_team_tab(
            context,
            arc_by_team[team_id],
            roster_by_team.get(team_id, []),
        ))

    home_rows, home_formats = build_home_rows(
        context, arc, get_record_book(),
        [title for title, _, _ in team_tabs],
    )
    return [(HOME_TAB, home_rows, home_formats)] + team_tabs


# ---------------------------------------------------------------------------
# Sheets write
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS = 5


def _sheets_call(label, fn):
    """Retry wrapper for transient Sheets API failures (quota 429s, 5xx).
    Mirrors almanac_write._sheets_call; extracting the two into a shared
    plumbing module is a follow-up (kept separate here so the
    golden-covered ESPN writer stays untouched)."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if attempt == _MAX_ATTEMPTS or status not in (429, 500, 502, 503):
                raise
            wait = min(2 ** attempt, 30)
            print(f"[cbs-almanac] {label}: Sheets API {status}; "
                  f"retry {attempt}/{_MAX_ATTEMPTS - 1} in {wait}s")
            time.sleep(wait)


def write_cbs_almanac(sheet_id, tabs):
    """Write every tab (create-or-replace by title) with light formatting:
    frozen header band, bold sections, sensible column widths."""
    client = _get_authorized_client()
    spreadsheet = _sheets_call('open', lambda: client.open_by_key(sheet_id))

    for title, rows, formats in tabs:
        width = max(len(r) for r in rows if r) if rows else 7
        try:
            worksheet = spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            worksheet = _sheets_call(
                f'create {title}',
                lambda t=title, w=width: spreadsheet.add_worksheet(
                    title=t, rows=max(len(rows) + 10, 40), cols=max(w, 8),
                ),
            )
        _sheets_call(f'clear {title}', worksheet.clear)
        _sheets_call(
            f'update {title}',
            lambda ws=worksheet, r=rows: ws.update(
                r, 'A1', value_input_option='RAW'),
        )
        _sheets_call(f'freeze {title}',
                     lambda ws=worksheet: ws.freeze(rows=2))
        if formats:
            _sheets_call(
                f'format {title}',
                lambda ws=worksheet, f=formats: ws.batch_format(f),
            )
        _apply_column_widths(spreadsheet, worksheet, title)
        print(f"[cbs-almanac] wrote tab: {title} ({len(rows)} rows)")


_HOME_WIDTHS = [(0, 1, 170), (1, 2, 230), (2, 3, 110), (3, 7, 95)]
_TEAM_WIDTHS = [(0, 1, 90), (1, 2, 210), (2, 3, 60), (3, 4, 120), (4, 6, 95)]


def _apply_column_widths(spreadsheet, worksheet, title):
    widths = _HOME_WIDTHS if title == HOME_TAB else _TEAM_WIDTHS
    requests = [{
        'updateDimensionProperties': {
            'range': {
                'sheetId': worksheet.id,
                'dimension': 'COLUMNS',
                'startIndex': start,
                'endIndex': end,
            },
            'properties': {'pixelSize': pixels},
            'fields': 'pixelSize',
        },
    } for start, end, pixels in widths]
    _sheets_call(f'widths {title}',
                 lambda: spreadsheet.batch_update({'requests': requests}))
