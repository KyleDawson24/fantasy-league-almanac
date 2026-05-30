"""output/almanac_render.py

Tier 2c.2 (v1.1.1): cell-level rendering and display constants for the
league almanac. Pure functions: take rows + specs, return display values
or row dicts. No SQL, no Sheets API.

Imports flow: almanac_data -> almanac_render. (Logic and write build on
top of render.) Render reads small helpers from almanac_data --
_fact_stat_column_name, HITTING_RECORD_LABELS, etc. -- because data is
where the seed-driven stat metadata lives.
"""

import math
import os

import almanac_data
from almanac_data import (
    _fact_stat_column_name,
    HITTING_RECORD_LABELS,
    slot_label,
)
from formatters import (
    fmt_avg, fmt_ip, fmt_value, fmt_record_value,
    format_top_scorer_stats_line,
    TOP_SCORER_STAT_DISPLAY, _SCORE_STAT_KEYS, _top_n_stats,
)
import records
import stat_catalog


HOME_TAB = 'Home'


RECORDS_TAB = 'Records'


TEAM_WEEKS_TAB = 'Matchup History'


DRAFT_TAB = 'Draft Recap'


# v1.2 draft tab: Best Value / Biggest Bust leaderboard columns.
DRAFT_VALUE_HEADER = ['Player', 'Team', 'Pick', 'Pts', 'Value']


HOME_HEADER = [
    'Slot', 'Team', 'Player', 'Fantasy Team', 'Owner',
    'Points', 'Slash', 'Stat Line',
]


# v1.2 (#23): single group label spanning the two Total-Pts deviation
# columns appended to the right-band All-League rows (alt player + total pts).
HOME_DEVIATION_LABEL = 'Total-Pts Best (incl. bench & FA)'


# v1.2 (#22/#23): thin left-band All-League Team (all-time) header.
HOME_ALLTIME_HEADER = ['Slot', 'Player', 'Points', 'ppg']


RECORDS_HEADER = [
    'Scope', 'Record', 'Holder', 'Fantasy Team', 'Owner',
    'Value', 'Season', 'Period', 'Details', 'Boxscore',
]


RECORDS_MATRIX_WIDTH = 12


RECORDS_MATRIX_DETAIL_HEADER = [
    'Record',
    'Holder', 'Owner', 'Value', 'Period', 'Details',
    '',
    'Holder', 'Owner', 'Value', 'Period', 'Details',
]


TEAM_HISTORY_DETAIL_HEADER = [
    'Tm', 'Slot', 'Player', 'Team',
    'RosterDays', 'Games', 'Active Points', 'Bench/IL Points', 'ppg',
    'Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB',
]


TEAM_ROSTER_HEADER = [
    *TEAM_HISTORY_DETAIL_HEADER,
    '',
    *TEAM_HISTORY_DETAIL_HEADER,
]


TEAM_ROSTER_MATRIX_WIDTH = len(TEAM_ROSTER_HEADER)


TEAM_HISTORY_HITTER_HEADER = '__hitter_header__'


TEAM_HISTORY_PITCHER_HEADER = '__pitcher_header__'


TEAM_HISTORY_MIXED_HEADER = '__mixed_header__'


TEAM_HISTORY_HITTER_STATS = ['Avg', 'OBP', 'Slg', 'HR', 'SB']


TEAM_HISTORY_PITCHER_STATS = ['W-L (Sv)', 'ERA', 'WHIP', 'K', 'BB']


TEAM_HISTORY_MIXED_STATS = ['Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB']


TEAM_WEEKS_BASE_HEADER = [
    'Sort Key', 'Season', 'Matchup', 'Team',
]


TEAM_WEEKS_SCORE_HEADER = [
    'Hitting Points', 'Pitching Points', 'Total Points', 'Margin', 'W', 'L',
    '', 'Matchup Hit', 'Matchup Pitch', 'Matchup Total',
    '', 'Lg Avg Hit', 'Lg Avg Pitch', 'Lg Avg Total',
]


TEAM_WEEKS_RARE_STATS = {'CYC', 'NH', 'PG', 'SHO'}


TEAM_WEEKS_WHITE_TO_GREEN_STATS = {'TRIPLES', 'B_IBB', 'CYC', 'CG', 'SHO', 'PG', 'PK'}


TEAM_WEEKS_WHITE_TO_RED_STATS = {'BLK'}


SLOT_ORDER = {
    'C': 10,
    '1B': 20,
    '2B': 30,
    '3B': 40,
    'SS': 50,
    'IF': 60,
    'LF': 70,
    'CF': 80,
    'RF': 90,
    'OF': 100,
    'DH': 110,
    'UTIL': 120,
    'SP': 130,
    'RP': 140,
    'P': 150,
}


def format_team_week_row(row, hitting_specs, pitching_specs, league_id=None,
                         schedule_lookup=None):
    """Project one team-week fact row into the archive tab layout."""
    schedule_lookup = schedule_lookup or {}
    result = row.get('result') or ''
    matchup_label = records.format_week_label(
        row.get('season_year'),
        row.get('matchup_period'),
        schedule_lookup,
    )
    return [
        row.get('sort_key') or '',
        row.get('season_year') or '',
        _period_boxscore_formula(
            matchup_label,
            league_id,
            row.get('season_year'),
            row.get('matchup_period'),
            row.get('team_id'),
        ),
        row.get('team_name') or '',
        *[_format_team_week_stat(row, spec) for spec in hitting_specs],
        '',
        *[_format_team_week_stat(row, spec) for spec in pitching_specs],
        '',
        _one_decimal(row.get('calculated_hitting_pts')),
        _one_decimal(row.get('calculated_pitching_pts')),
        _one_decimal(row.get('calculated_points')),
        _one_decimal(row.get('calculated_margin')),
        1 if result == 'W' else '',
        1 if result == 'L' else '',
        '',
        _one_decimal(row.get('matchup_calculated_hitting_pts')),
        _one_decimal(row.get('matchup_calculated_pitching_pts')),
        _one_decimal(row.get('matchup_calculated_points')),
        '',
        _one_decimal(row.get('league_avg_hitting_points')),
        _one_decimal(row.get('league_avg_pitching_points')),
        _one_decimal(row.get('league_avg_total_points')),
    ]


def _records_matrix_scope_header(section_title):
    return [
        section_title,
        'Current Season', '', '', '', '',
        '',
        'All-Time', '', '', '', '',
    ]


def _team_history_scope_header():
    left_span = len(TEAM_HISTORY_DETAIL_HEADER)
    return [
        'Current Season',
        *([''] * (left_span - 1)),
        '',
        'All-Time',
        *([''] * (left_span - 1)),
    ]


def format_team_history_matrix_row(label, current_row=None, all_time_row=None):
    if label == TEAM_HISTORY_HITTER_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_HITTER_STATS)
    if label == TEAM_HISTORY_PITCHER_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_PITCHER_STATS)
    if label == TEAM_HISTORY_MIXED_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_MIXED_STATS)
    return [
        *_team_history_side_cells(current_row),
        '',
        *_team_history_side_cells(all_time_row),
    ]


def _team_history_section_header_row(stat_labels):
    side = [''] * len(TEAM_HISTORY_DETAIL_HEADER)
    side[9:] = stat_labels
    return [*side, '', *side]


def _team_history_display_row(row, label, display_slot=None, active_games=None,
                              active_points=None):
    active_games = int(active_games if active_games is not None else row.get('active_games') or 0)
    active_points = _one_decimal(
        active_points if active_points is not None else row.get('active_points')
    )
    stat_line = _team_history_stat_line(row, display_slot or label)
    return {
        'slot_label': label,
        'display_slot': display_slot or label,
        'player': row.get('display_name') or row.get('player_name') or '',
        'pro_team': row.get('pro_team') or '',
        'current_fantasy_team': row.get('current_fantasy_team') or '',
        'rostered_days': int(row.get('rostered_days') or 0),
        'active_games': active_games,
        'active_points': _round_half_up(active_points),
        'bench_il_points': _round_half_up(float(row.get('bench_il_points') or 0)),
        'points_per_active_game': (
            f"{active_points / active_games:.2f}" if active_games else ''
        ),
        **stat_line,
    }


def _empty_team_history_display_row():
    return {
        'display_slot': '',
        'player': '',
        'pro_team': '',
        'current_fantasy_team': '',
        'rostered_days': '',
        'active_games': '',
        'active_points': '',
        'bench_il_points': '',
        'points_per_active_game': '',
        'stat_1': '',
        'stat_2': '',
        'stat_3': '',
        'stat_4': '',
        'stat_5': '',
    }


def _team_history_side_cells(row):
    row = row or _empty_team_history_display_row()
    return [
        row.get('current_fantasy_team') or '',
        row.get('display_slot') or '',
        row.get('player') or '',
        row.get('pro_team') or '',
        row.get('rostered_days'),
        row.get('active_games'),
        row.get('active_points'),
        row.get('bench_il_points'),
        row.get('points_per_active_game'),
        row.get('stat_1'),
        row.get('stat_2'),
        row.get('stat_3'),
        row.get('stat_4'),
        row.get('stat_5'),
    ]


def _team_history_stat_line(row, display_slot):
    if _team_history_is_pitcher(row, display_slot):
        return {
            'stat_1': _pitching_decision_display(row, display_slot),
            'stat_2': _pitching_rate(row, 'era'),
            'stat_3': _pitching_rate(row, 'whip'),
            'stat_4': int(row.get('k') or 0),
            'stat_5': int(row.get('p_bb') or 0),
        }
    return {
        'stat_1': _hitting_rate(row, 'avg'),
        'stat_2': _hitting_rate(row, 'obp'),
        'stat_3': _hitting_rate(row, 'slg'),
        'stat_4': int(row.get('hr') or 0),
        'stat_5': int(row.get('sb') or 0),
    }


def _team_history_is_pitcher(row, display_slot):
    slots = _display_slot_tokens(display_slot)
    if not slots:
        slots = _display_slot_tokens(row.get('active_slots_played'))
    if 'SP' in slots or 'RP' in slots:
        return True
    if 'P' in slots:
        return True
    pitching_volume = sum(row.get(k) or 0 for k in ('outs', 'k', 'sv', 'w', 'l'))
    hitting_volume = sum(row.get(k) or 0 for k in ('ab', 'h', 'hr', 'sb'))
    return pitching_volume > hitting_volume


def _display_slot_tokens(value):
    cleaned = str(value or '').replace('-', ',').replace(' ', ',')
    return {part.strip() for part in cleaned.split(',') if part.strip()}


def _pitching_decision_display(row, display_slot):
    wins = int(row.get('w') or 0)
    losses = int(row.get('l') or 0)
    saves = int(row.get('sv') or 0)
    # v1.1.2: W-L when the pitcher logged no saves (6-4); W-L-Sv when
    # they did (2-1-15). Replaces the "decisions vs. saves, show whichever
    # is larger" rule, which dropped W-L for closers and saves for
    # swingmen who had both.
    if saves > 0:
        return f"{wins}-{losses}-{saves}"
    return f"{wins}-{losses}"


def _hitting_rate(row, stat_name):
    at_bats = row.get('ab') or 0
    if not at_bats:
        return ''
    hits = row.get('h') or 0
    if stat_name == 'avg':
        return _rate_as_whole_number(hits / at_bats)
    if stat_name == 'obp':
        denominator = at_bats + (row.get('b_bb') or 0) + (row.get('hbp') or 0) + (row.get('sf') or 0)
        return _rate_as_whole_number(
            (hits + (row.get('b_bb') or 0) + (row.get('hbp') or 0)) / denominator
        ) if denominator else ''
    if stat_name == 'slg':
        return _rate_as_whole_number((row.get('tb') or 0) / at_bats)
    return ''


def _rate_as_whole_number(value):
    return f"{int(round(value * 1000)):03d}"


def _round_half_up(value):
    if value < 0:
        return math.ceil(value - 0.5)
    return math.floor(value + 0.5)


def _pitching_rate(row, stat_name):
    outs = row.get('outs') or 0
    if not outs:
        return ''
    innings = outs / 3
    if stat_name == 'era':
        return f"{((row.get('er') or 0) * 9 / innings):.2f}"
    if stat_name == 'whip':
        return f"{(((row.get('p_bb') or 0) + (row.get('p_h') or 0)) / innings):.2f}"
    return ''


def _inactive_position_display(row):
    return row.get('active_slots_played') or row.get('position') or ''


def _compact_inactive_slot(slot, position):
    clean_position = ','.join(part.strip() for part in str(position or '').split(',') if part.strip())
    if clean_position:
        return f'{slot} - {clean_position}'
    return slot


def _all_league_slash_line(row):
    """Compact slash line for the All-League Team. Hitters: AVG/OBP/SLG
    as leading-dot 3-digit (e.g. .294/.390/.559). Pitchers: W-L-Sv/ERA/WHIP
    (e.g. 6-4-2/3.00/0.82). Reuses the team-tab rate helpers, so the
    underlying values stay consistent with the per-team pages."""
    slot = str(row.get('lineup_slot') or row.get('slot_label') or '')
    if slot.startswith(('SP', 'RP', 'P')):
        return (
            f"{_pitching_decision_display(row, slot)}"
            f"/{_pitching_rate(row, 'era')}"
            f"/{_pitching_rate(row, 'whip')}"
        )
    return (
        f"{_dotted_rate(_hitting_rate(row, 'avg'))}"
        f"/{_dotted_rate(_hitting_rate(row, 'obp'))}"
        f"/{_dotted_rate(_hitting_rate(row, 'slg'))}"
    )


def _dotted_rate(rate):
    """Prefix a leading dot to a no-dot 3-digit rate ('294' -> '.294').
    Empty stays empty so a no-AB hitter still renders '//', not './/.'."""
    return f".{rate}" if rate else ''


def format_all_league_team_row(row, league_id=None):
    """Project one selected slot row into the Home tab table shape."""
    season = row.get('season_year')
    matchup_period = row.get('matchup_period')
    team_id = row.get('team_id')
    is_season_row = row.get('period_label') == 'Season'
    points = _one_decimal(row.get('platform_points'))
    # v1.2: embed the boxscore as a hyperlink on the Points cell (week
    # rows only) so we keep both Slash and the verbose Stat Line without
    # a separate Boxscore column. Season / all-time rows span multiple
    # matchups, so there's no single boxscore to link.
    points_cell = points if is_season_row else _period_boxscore_formula(
        points, league_id, season, matchup_period, team_id,
    )
    return [
        row.get('slot_label') or row.get('lineup_slot') or '',
        row.get('pro_team') or '',
        row.get('display_name') or row.get('player_name') or '',
        row.get('team_abbrev') or row.get('team_name') or '',
        row.get('owner_name') or '',
        points_cell,
        _all_league_slash_line(row),
        format_top_scorer_stats_line(row),
    ]


def format_all_league_thin_row(row):
    """Project one optimal-team pick into the thin left-band shape:
    Slot | Player | Pts | ppg.

    Used by the Home all-time All-League Team (#22). ppg = points /
    games_played; games_played comes from _enrich_optimal_team_with_stats
    and is active-games when the team was built points_type='active'
    (the all-time team is), so ppg reads "points per active game" --
    the same convention as the per-team tabs' points_per_active_game.
    """
    slot = row.get('slot_label') or row.get('lineup_slot') or ''
    player = row.get('display_name') or row.get('player_name') or ''
    if not player:
        return [slot, '', '', '']
    pts_raw = row.get('platform_points') or 0
    # Whole number -- 1-decimal precision is overkill at the all-time scale.
    points = _round_half_up(pts_raw)
    games = int(row.get('games_played') or 0)
    ppg = f"{(pts_raw / games):.2f}" if games else ''
    return [slot, player, points, ppg]


def format_all_league_team_row_with_deviation(row, deviation_pick, league_id=None):
    """Right-band All-League row: the 8 standard columns plus the two
    Total-Pts deviation columns (#23).

    ``deviation_pick`` is the points_type='all' pick for this slot when it
    is a DIFFERENT player than the active pick (the caller decides this;
    a same-player points-only delta passes None). The two appended cells
    are the alternate player's name and their total (active+inactive+FA)
    points. Both blank when there's no deviation at this slot.
    """
    base = format_all_league_team_row(row, league_id=league_id)
    if not deviation_pick:
        return [*base, '', '']
    dev_player = deviation_pick.get('display_name') or deviation_pick.get('player_name') or ''
    dev_points = _one_decimal(deviation_pick.get('platform_points'))
    return [*base, dev_player, dev_points]


def home_nav_link(label, tab_title=None, gid_map=None):
    """Render one Home nav cell (#23/#25).

    With a gid for ``tab_title`` in ``gid_map`` (live write), emit an
    in-sheet =HYPERLINK to that tab's A1. Otherwise plain text -- the TSV
    preview (no gids exist) or a not-yet-built target like Draft Recap
    (tab_title=None).
    """
    gid = (gid_map or {}).get(tab_title) if tab_title is not None else None
    if gid is None:
        return label
    safe = str(label).replace('"', '""')
    return f'=HYPERLINK("#gid={gid}&range=A1", "{safe}")'


# -------------------------------------------------------------------------
# Draft Recap tab (v1.2): round x team board + Best Value / Biggest Bust
# leaderboards. value_delta = overall_pick - points_rank (a steal is a high
# positive; a bust is a large negative). (K) marks keeper picks.
# -------------------------------------------------------------------------


def _draft_pick_label(pick):
    """Compact draft-position label, e.g. 'R14 #195' (round + overall pick)."""
    return f"R{pick.get('round_num')} #{pick.get('overall_pick')}"


def _draft_player_label(pick):
    """Player name with a keeper marker."""
    name = pick.get('player_name') or ''
    return f"{name} (K)" if pick.get('keeper') else name


def format_draft_value_row(pick):
    """One Best-Value / Biggest-Bust leaderboard row:
    Player (+K) | Team | Pick | Pts | Value(+/-)."""
    value = pick.get('value_delta')
    return [
        _draft_player_label(pick),
        pick.get('team_abbrev') or '',
        _draft_pick_label(pick),
        _one_decimal(pick.get('season_points')),
        f"{int(value):+d}" if value is not None else '',
    ]


def format_draft_board_cell(pick):
    """Round x team grid cell: the drafted player, keeper-marked. Blank for
    an unfilled (round, team) slot (shouldn't occur in a full keeper draft)."""
    return _draft_player_label(pick) if pick else ''


def format_record_matrix_row(spec, current_record=None, all_time_record=None,
                             league_id=None, display_map=None, schedule_lookup=None):
    """Project current/all-time holders into one side-by-side record row."""
    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()
    return [
        spec.get('label') or display_map.get(spec.get('stat_name'), spec.get('stat_name')),
        *_format_record_side(
            current_record,
            scope='current_season',
            league_id=league_id,
            display_map=display_map,
            schedule_lookup=schedule_lookup,
        ),
        '',
        *_format_record_side(
            all_time_record,
            scope='all_time',
            league_id=league_id,
            display_map=display_map,
            schedule_lookup=schedule_lookup,
        ),
    ]


def _format_record_side(record, scope, league_id=None, display_map=None, schedule_lookup=None):
    """Format one current-season or all-time side of a matrix row."""
    if not record:
        return ['', '', '', '', '']

    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()

    season = record.get('season_year')
    matchup_period = record.get('matchup_period')
    if record.get('is_collapsed'):
        holder = _collapsed_holder(record)
        owner = _collapsed_owner(record)
        period = _collapsed_period(record, schedule_lookup, scope=scope)
    elif record.get('entity_grain') == 'player':
        holder = record.get('display_name') or record.get('player_name') or ''
        owner = record.get('owner_name') or ''
        period = (
            records.format_week_label(season, matchup_period, schedule_lookup)
            if season is not None and matchup_period is not None
            else ''
        )
    else:
        holder = record.get('team_abbrev') or record.get('team_name') or ''
        owner = record.get('owner_name') or ''
        period = (
            records.format_week_label(season, matchup_period, schedule_lookup)
            if season is not None and matchup_period is not None
            else ''
        )
    if scope == 'all_time' and not record.get('is_collapsed') and period and season:
        period = f"{period}: {season}"
    period = _period_boxscore_formula(
        period, league_id, season, matchup_period, record.get('team_id')
    )
    return [
        holder,
        owner,
        _format_record_value(record.get('stat_name'), record.get('stat_value')),
        period,
        _record_details(record, display_map),
    ]


def format_record_row(record, scope_label, league_id=None, display_map=None,
                      polarity_map=None, schedule_lookup=None):
    """Project one record holder into the curated Records tab shape."""
    display_map = display_map or stat_catalog.get_display_map()
    polarity_map = polarity_map or stat_catalog.get_polarity_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()

    grain = record.get('entity_grain')
    stat_name = record.get('stat_name')
    direction = record.get('record_direction')
    season = record.get('season_year')
    matchup_period = record.get('matchup_period')
    record_label = _record_label(record, display_map, polarity_map)

    if record.get('is_collapsed'):
        holder = _collapsed_holder(record)
        fantasy_team = ''
        owner = ''
        boxscore = ''
    elif grain == 'player':
        holder = record.get('display_name') or record.get('player_name') or ''
        fantasy_team = record.get('team_name') or ''
        owner = record.get('owner_name') or ''
        boxscore = boxscore_formula(league_id, season, matchup_period, record.get('team_id'))
    else:
        holder = record.get('team_name') or ''
        fantasy_team = record.get('team_abbrev') or ''
        owner = record.get('owner_name') or ''
        boxscore = boxscore_formula(league_id, season, matchup_period, record.get('team_id'))

    period = (
        records.format_week_label(season, matchup_period, schedule_lookup)
        if season is not None and matchup_period is not None
        else ''
    )

    return [
        scope_label,
        record_label,
        holder,
        fantasy_team,
        owner,
        _format_record_value(stat_name, record.get('stat_value')),
        season or '',
        period,
        _record_details(record, display_map),
        boxscore,
    ]


def _record_label(record, display_map, polarity_map):
    """Build a compact label like 'Best Team Total Points'."""
    grain = (record.get('entity_grain') or '').title()
    stat_name = record.get('stat_name')
    direction = record.get('record_direction')
    outcome = records.best_or_worst_label(stat_name, direction, polarity_map)
    stat_label = display_map.get(stat_name, stat_name)
    return f"{outcome} {grain} {stat_label}".strip()


def _collapsed_holder(record):
    """Render a collapsed top tied tier."""
    holders = record.get('holders') or []
    grain = record.get('entity_grain')
    if holders:
        if grain == 'player':
            return ', '.join(h.get('display_name') or '' for h in holders)
        return ', '.join(h.get('team_abbrev') or h.get('team_name') or '' for h in holders)
    unit = 'players' if grain == 'player' else 'teams'
    return f"{record.get('tie_count', 0)} {unit} tied"


def _collapsed_owner(record):
    """Render owner names for a small collapsed tied tier."""
    holders = record.get('holders') or []
    if not holders:
        return ''
    owners = [h.get('owner_name') or '' for h in holders]
    return ', '.join(owners)


def _collapsed_season(record):
    """Render seasons for collapsed tiers, compacting single-season ties."""
    holders = record.get('holders') or []
    if not holders:
        return record.get('season_year') or ''
    seasons = [str(h.get('season_year')) for h in holders if h.get('season_year')]
    unique = []
    for season in seasons:
        if season not in unique:
            unique.append(season)
    return unique[0] if len(unique) == 1 else ', '.join(unique)


def _collapsed_period(record, schedule_lookup, scope='current_season'):
    """Render holder-period pairs for small collapsed tied tiers."""
    holders = record.get('holders') or []
    if not holders:
        season = record.get('season_year')
        matchup_period = record.get('matchup_period')
        return (
            _period_label(season, matchup_period, schedule_lookup, scope)
            if season is not None and matchup_period is not None
            else ''
        )

    parts = []
    for holder in holders:
        season = holder.get('season_year')
        matchup_period = holder.get('matchup_period')
        if season is None or matchup_period is None:
            continue
        name = (
            holder.get('team_abbrev')
            or holder.get('display_name')
            or holder.get('team_name')
            or holder.get('player_name')
            or ''
        )
        period = _period_label(season, matchup_period, schedule_lookup, scope)
        parts.append(f"{name} {period}".strip())
    return '; '.join(parts)


def _period_label(season, matchup_period, schedule_lookup, scope):
    label = records.format_week_label(season, matchup_period, schedule_lookup)
    if scope == 'all_time' and season:
        return f"{label}: {season}"
    return label


def _record_details(record, display_map):
    """Render contributor context for a record row."""
    rate_detail = _rate_qualifier_detail(record)
    if record.get('is_collapsed'):
        value = _format_record_value(record.get('stat_name'), record.get('stat_value'))
        detail = f"Top tier tied at {value}"
        return f"{detail}; {rate_detail}" if rate_detail else detail

    if rate_detail:
        return rate_detail

    contributors = record.get('contributors') or []
    if not contributors:
        return ''

    parts = []
    if record.get('entity_grain') == 'team':
        stat_name = record.get('stat_name')
        for item in contributors[:3]:
            value = fmt_record_value(stat_name, item.get('stat_value'))
            parts.append(f"{item.get('display_name')}: {value}")
    else:
        for item in contributors[:3]:
            stat_name = item.get('stat_name')
            label = _detail_stat_label(stat_name, display_map)
            value = fmt_record_value(stat_name, item.get('count_value'))
            parts.append(f"{value} {label}")
    return ', '.join(parts)


def _detail_stat_label(stat_name, display_map):
    label = display_map.get(stat_name, stat_name)
    replacements = {
        'Innings Pitched': 'IP',
        'Strikeouts (Pitcher)': 'K',
        'Strikeouts (Batter)': 'K',
        'Home Runs': 'HR',
        'Quality Starts': 'Quality Starts',
        'RBIs': 'RBI',
    }
    return replacements.get(label, label)


def _rate_qualifier_detail(record):
    """Render AB/IP qualification context for team rate records."""
    stat_name = record.get('stat_name')
    qualifier = record.get('qualifier_value')
    if qualifier is None:
        return ''
    if stat_name in {'AVG', 'OBP', 'SLG'}:
        return f"{int(qualifier)} AB"
    if stat_name in {'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'}:
        return f"{fmt_ip(qualifier)} IP"
    return ''


def _format_record_value(stat_name, value):
    """Almanac-specific record value rendering."""
    if value is None:
        return ''
    if stat_name in {'AVG', 'OBP', 'SLG'}:
        return fmt_avg(value)
    if stat_name in {'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'}:
        return f"{value:.2f}"
    if str(stat_name or '').startswith('LINEUP_SLOT_POINTS__'):
        return f"{value:.1f}"
    return fmt_record_value(stat_name, value)


def format_team_roster_row(row, league_id=None):
    """Project one current roster row into a team active-stat table row."""
    slot = slot_label(
        row.get('lineup_slot'),
        int(row.get('slot_rank') or 1),
        int(row.get('slots_to_fill') or 1),
    )
    if row.get('is_empty_slot'):
        return [slot, *([''] * (len(TEAM_ROSTER_HEADER) - 1))]

    return [
        slot,
        row.get('pro_team') or '',
        row.get('display_name') or row.get('player_name') or '',
        row.get('position') or '',
        _one_decimal(row.get('active_points')),
        int(row.get('active_weeks') or 0),
        int(row.get('active_days') or 0),
        int(row.get('rostered_days') or 0),
        _one_decimal(row.get('inactive_points')),
        int(row.get('hr') or 0),
        int(row.get('rbi') or 0),
        int(row.get('r') or 0),
        int(row.get('sb') or 0),
        int(row.get('w') or 0),
        int(row.get('sv') or 0),
        int(row.get('hld') or 0),
        int(row.get('k') or 0),
        fmt_ip(row.get('outs')),
    ]


def team_tab_title(row):
    """Return a compact worksheet title for a team roster page."""
    team_id = row.get('team_id')
    name = row.get('team_abbrev') or row.get('team_name') or str(team_id or '')
    return _safe_sheet_title(name)


def boxscore_formula(league_id, season_year, matchup_period, team_id):
    """Return a Google Sheets HYPERLINK formula for an ESPN boxscore."""
    if not (league_id and season_year and matchup_period and team_id):
        return ''
    url = _boxscore_url(league_id, season_year, matchup_period, team_id)
    return f'=HYPERLINK("{url}", "boxscore")'


def _period_boxscore_formula(label, league_id, season_year, matchup_period, team_id):
    """Return a HYPERLINK formula whose visible text is the period label."""
    if not label:
        return ''
    if not (league_id and season_year and matchup_period and team_id):
        return label
    url = _boxscore_url(league_id, season_year, matchup_period, team_id)
    safe_label = str(label).replace('"', '""')
    return f'=HYPERLINK("{url}", "{safe_label}")'


def _boxscore_url(league_id, season_year, matchup_period, team_id):
    return (
        'https://fantasy.espn.com/baseball/boxscore?'
        f'leagueId={league_id}&matchupPeriodId={matchup_period}'
        f'&seasonId={season_year}&teamId={team_id}&view=matchup'
    )


# _team_week_stat_sort_key moved to almanac_data.py (Tier 2c.1).


def _team_week_specs_for_category(stat_specs, category):
    return [spec for spec in stat_specs if spec.get('stat_category') == category]


def _team_week_stat_headers(stat_specs):
    return [_team_week_stat_header(spec) for spec in stat_specs]


def _team_week_stat_header(spec):
    stat_name = spec.get('stat_name')
    if stat_name == 'OUTS':
        return 'IP'
    return spec.get('abbrev') or spec.get('display_name') or stat_name


def _format_team_week_stat(row, spec):
    stat_name = spec.get('stat_name')
    value = row.get(_fact_stat_column_name(stat_name))
    if stat_name == 'OUTS':
        return fmt_ip(value)
    if value is None:
        return ''
    if float(value).is_integer():
        return int(value)
    return round(value, 3)


# _team_week_good_record_direction moved to almanac_data.py (Tier 2c.1).


def _is_rare_team_week_stat(stat_name):
    return stat_name in TEAM_WEEKS_RARE_STATS


def _slot_sort_key(slot):
    return (SLOT_ORDER.get(slot, 999), slot)


def _one_decimal(value):
    return round(value or 0, 1)


def _safe_sheet_title(title):
    bad_chars = set('[]:*?/\\')
    cleaned = ''.join('-' if c in bad_chars else c for c in str(title))
    cleaned = cleaned.strip("'").strip()
    return cleaned[:100] or 'Sheet'


def _format_sheet_date(value):
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%b %-d, %Y') if os.name != 'nt' else value.strftime('%b %#d, %Y')
    text = str(value)
    try:
        from datetime import date
        return date.fromisoformat(text[:10]).strftime('%b %#d, %Y' if os.name == 'nt' else '%b %-d, %Y')
    except ValueError:
        return text


def _is_pitcher_display_slot(slot):
    slot_text = str(slot or '')
    if not slot_text:
        return False
    if slot_text.startswith(('SP', 'RP', 'P ')):
        return True
    return any(token in slot_text for token in ('- SP', '- RP', '- P'))


def _is_hitter_display_slot(slot):
    slot_text = str(slot or '')
    if not slot_text:
        return False
    if slot_text in {'Slot', 'Avg', 'W-L (Sv)', 'Avg|W-L-Sv'}:
        return False
    return not _is_pitcher_display_slot(slot_text)


def _is_active_display_slot(slot):
    slot_text = str(slot or '')
    return bool(slot_text) and not slot_text.startswith(('BE', 'IL', 'Other'))
