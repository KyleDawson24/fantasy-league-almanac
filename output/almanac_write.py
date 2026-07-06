"""output/almanac_write.py

Tier 2c.4 (v1.1.1): Google Sheets orchestration for the league almanac.

Owns the gspread / Sheets API surface: opening the spreadsheet, replacing
tab contents, applying cell formatting (color scales, frozen rows,
column widths, conditional record highlighting), and the retry wrapper
that handles transient Sheets quota errors during a full almanac write.

Dependencies (downward only): almanac_data, almanac_render, almanac_logic.
Write is the consumer of everything else -- it gathers all the row data
through the other three modules and pushes it to the spreadsheet.
"""

import math
import os
import re
import time

import gspread

import almanac_data
import almanac_logic
import almanac_render
import records
from almanac_data import (
    get_almanac_records,
    get_draft_board,
    get_team_standings,
    get_team_slot_points,
    get_current_team_roster_stats,
    get_latest_matchup_period,
    get_roster_slot_capacities,
    get_team_roster_history_stats,
    get_team_week_record_marks,
    get_team_week_stat_specs,
    get_team_weeks,
    _fact_stat_column_name,
)
from almanac_logic import (
    SCORE_RECORD_SPECS,
    build_advanced_standings_tab_rows,
    build_draft_board_color_grid,
    build_draft_tab_rows,
    build_home_tab_rows,
    build_records_tab_rows,
    build_team_history_tabs,
    build_team_weeks_tab_rows,
    expand_team_roster_rows,
)
from almanac_render import (
    ADVANCED_STANDINGS_TAB,
    DRAFT_TAB,
    HOME_TAB,
    RECORDS_TAB,
    RECORDS_MATRIX_DETAIL_HEADER,
    RECORDS_MATRIX_WIDTH,
    TEAM_HISTORY_DETAIL_HEADER,
    TEAM_HISTORY_HITTER_HEADER,
    TEAM_HISTORY_PITCHER_HEADER,
    TEAM_HISTORY_MIXED_HEADER,
    TEAM_HISTORY_HITTER_STATS,
    TEAM_HISTORY_PITCHER_STATS,
    TEAM_HISTORY_MIXED_STATS,
    TEAM_ROSTER_HEADER,
    TEAM_ROSTER_MATRIX_WIDTH,
    TEAM_WEEKS_BASE_HEADER,
    TEAM_WEEKS_RARE_STATS,
    TEAM_WEEKS_SCORE_HEADER,
    TEAM_WEEKS_TAB,
    TEAM_WEEKS_WHITE_TO_GREEN_STATS,
    TEAM_WEEKS_WHITE_TO_RED_STATS,
    _is_active_display_slot,
    _is_hitter_display_slot,
    _is_pitcher_display_slot,
    _is_rare_team_week_stat,
    _safe_sheet_title,
    _team_week_specs_for_category,
)
from sheets_writer import _get_authorized_client


# PITCHING_STAT_ORDER moved to almanac_data.py (Tier 2c.1). Re-exported
# below for backward compat.


def write_almanac(sheet_id, season_year=None, matchup_period=None):
    """Write the v1.1 almanac Home tab.

    If `season_year` / `matchup_period` are omitted, the latest loaded
    matchup_period is used. The Sheets writer is intentionally separate
    from `sheets_writer.write_records()` while the almanac surface is
    being built out, so the legacy records sink remains stable.
    """
    if season_year is None or matchup_period is None:
        season_year, matchup_period = get_latest_matchup_period()

    league_id = os.getenv('LEAGUE_ID')
    # Tier 2c.5 (v1.1.1): season-to-date is get_all_league_team(matchup_
    # period=None). v1.2 (#23): all Home datasets come from one fetch so
    # this live path can't drift from the preview path.
    home_data = almanac_data.get_home_tab_data(season_year, matchup_period)
    schedule_lookup = records.load_schedule_lookup()
    records_rows = build_records_tab_rows(
        all_time_records=get_almanac_records('all_time'),
        current_season_records=get_almanac_records('current_season'),
        league_id=league_id,
        schedule_lookup=schedule_lookup,
    )
    team_week_stat_specs = get_team_week_stat_specs()
    team_week_rows = get_team_weeks(team_week_stat_specs)
    team_week_record_marks = get_team_week_record_marks(team_week_stat_specs)
    team_weeks_tab_rows = build_team_weeks_tab_rows(
        team_week_rows,
        team_week_stat_specs,
        league_id=league_id,
        schedule_lookup=schedule_lookup,
    )
    team_pages = build_team_history_tabs(
        get_team_roster_history_stats(season_year),
        season_year=season_year,
        league_id=league_id,
        slot_caps=get_roster_slot_capacities(season_year, include_inactive=True),
    )
    draft_board = get_draft_board(season_year)
    draft_tab_rows = build_draft_tab_rows(draft_board, season_year, league_id=league_id)
    draft_color_grid = build_draft_board_color_grid(draft_board)
    standings_tab_rows = build_advanced_standings_tab_rows(
        get_team_standings(season_year, team_week_stat_specs),
        get_team_slot_points(season_year),
        team_week_stat_specs,
        season_year,
    )
    client = _get_authorized_client()
    spreadsheet = client.open_by_key(sheet_id)

    # Two-pass write (#25). Pass 1: create/write every non-Home tab so
    # their gids exist; capture each worksheet. Pass 2: read the gids
    # straight off those worksheets, render the Home nav as in-sheet
    # =HYPERLINK("#gid=...&range=A1") formulas, and write Home last.
    # Reading gids at write time -- never hardcoding tab URLs -- keeps this
    # portable: it works on a brand-new sheet for any league, no manual
    # copy-paste of tab links.
    records_ws = _replace_records_tab(spreadsheet, records_rows)
    matchup_ws = _replace_team_weeks_tab(
        spreadsheet,
        team_weeks_tab_rows,
        team_week_stat_specs,
        source_rows=team_week_rows,
        record_marks=team_week_record_marks,
    )
    _delete_prefixed_team_tabs(spreadsheet, {title for title, _ in team_pages})
    team_worksheets = [
        _replace_team_tab(spreadsheet, title, team_page_rows)
        for title, team_page_rows in team_pages
    ]
    draft_ws = _replace_draft_tab(spreadsheet, draft_tab_rows, color_grid=draft_color_grid)
    standings_ws = _replace_advanced_standings_tab(
        spreadsheet, standings_tab_rows, team_week_stat_specs,
    )

    nav_targets = {
        ws.title: ws.id
        for ws in (records_ws, matchup_ws, draft_ws, standings_ws, *team_worksheets)
        if ws is not None
    }
    rows = build_home_tab_rows(
        **home_data,
        season_year=season_year,
        matchup_period=matchup_period,
        team_titles=[title for title, _ in team_pages],
        league_id=league_id,
        nav_targets=nav_targets,
    )
    _replace_home_tab(spreadsheet, rows)

    _sort_almanac_tabs(spreadsheet, [
        HOME_TAB, RECORDS_TAB, TEAM_WEEKS_TAB, ADVANCED_STANDINGS_TAB,
        *[title for title, _ in team_pages], DRAFT_TAB,
    ])

    print(
        f"[almanac] wrote {len(home_data['weekly_rows'])} weekly + "
        f"{len(home_data['season_rows'])} season-to-date all-league rows "
        f"{len(records_rows)} records-tab rows, {len(team_week_rows)} team-week rows "
        f"and {len(team_pages)} team roster tabs "
        f"for {season_year} MP{matchup_period} to sheet {sheet_id}"
    )


_HOME_LEFT_SECTION_LABELS = {
    'Navigate', 'Points Glossary', 'All-League Team: All-Time',
}


def _replace_home_tab(spreadsheet, rows):
    """Clear/create Home and write the two-band almanac front page (#23)."""
    width = max((len(row) for row in rows), default=20)
    try:
        worksheet = spreadsheet.worksheet(HOME_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {HOME_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=HOME_TAB, rows=max(len(rows) + 10, 50), cols=max(width, 20),
            ),
        )

    _sheets_call(f'clear {HOME_TAB}', worksheet.clear)
    _sheets_call(
        f'update {HOME_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    # First-pass polish for the two-band layout. Deliberately restrained --
    # the live Sheet gets a hand pass (merges, widths, color). Byte-diff
    # doesn't cover formatting, so keep this defensive.
    try:
        last_col = _a1_col(width)
        _sheets_call(f'freeze {HOME_TAB}', lambda: worksheet.freeze(rows=3))
        _apply_home_tab_dimensions(spreadsheet, worksheet)
        formats = [
            {  # title banner
                'range': f'A1:{last_col}1',
                'format': {'textFormat': {'bold': True, 'fontSize': 14}},
            },
            {  # scoring callout
                'range': f'A2:{last_col}2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.90, 'green': 0.94, 'blue': 0.98},
                },
            },
            # Points number formats. Left all-time Points (C) is whole --
            # 1-decimal is overkill at the all-time scale; right Points (K)
            # and deviation total pts (O) stay one decimal. Number format
            # only touches numeric cells, harmless on the text/hyperlink
            # cells those columns also contain.
            {'range': 'C:C', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}}},
            # ppg (D) -- 2 decimals. The value is written as a string but
            # USER_ENTERED coerces it to a number and would drop a trailing
            # zero ("4.60" -> 4.6); the format pins it back to 2 places.
            {'range': 'D:D', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.00'}}},
            {'range': 'K:K', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
            {'range': 'O:O', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
        ]
        formats.extend(_home_label_formats(rows, last_col))
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped: {exc}")


def _apply_home_tab_dimensions(spreadsheet, worksheet):
    """Set Home column widths (#23 live polish). Cols A-K + N-O are sized
    to the two-band content; L/M (right Slash / Stat Line) and P+ keep the
    default. Indices are 0-based: A=0 ... O=14."""
    sheet_id = worksheet.id
    widths = [
        (0, 100), (1, 125), (2, 100), (3, 50), (4, 100), (5, 40), (6, 40),
        (7, 150), (8, 100), (9, 125), (10, 50), (13, 150), (14, 50),
    ]
    requests = [
        _column_width_request(sheet_id, idx, idx + 1, px) for idx, px in widths
    ]
    _sheets_batch_update(spreadsheet, f'home dimensions {worksheet.title}', requests)


def _home_label_formats(rows, last_col):
    """Bold the two-band Home section labels + table headers. Positions are
    dynamic, so scan by marker (#23). Best-effort: keep these marker
    strings in sync with build_home_tab_rows' left-band labels."""
    formats = []
    for row_number, row in enumerate(rows, 1):
        first = row[0] if len(row) > 0 else ''
        right = row[5] if len(row) > 5 else ''
        if first in _HOME_LEFT_SECTION_LABELS or (
            first == 'Slot' and len(row) > 1 and row[1] == 'Player'
        ):
            formats.append({
                'range': f'A{row_number}:D{row_number}',
                'format': {'textFormat': {'bold': True}},
            })
        if isinstance(right, str) and right.startswith('All-League Team'):
            formats.append({
                'range': f'F{row_number}:{last_col}{row_number}',
                'format': {'textFormat': {'bold': True}},
            })
        elif right == 'Slot':
            formats.append({
                'range': f'F{row_number}:{last_col}{row_number}',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            })
    return formats


# Draft board team columns start at index 4 (after Rd / Min / Median / Max).
_DRAFT_BOARD_TEAM_START = 4
_DRAFT_HEADER_BG = {'red': 0.90, 'green': 0.94, 'blue': 0.98}
_DRAFT_BOARD_HEADER_BG = {'red': 0.12, 'green': 0.20, 'blue': 0.30}


def _reapply_formula_cells(worksheet, rows):
    """Re-send any formula cells (values starting with '=') with USER_ENTERED.

    The team and draft tabs are bulk-written RAW so signed / zero-padded value
    strings ("+253", "040", "3.00") survive verbatim -- but RAW also keeps
    Sheets from parsing a formula, so the bref HYPERLINK player links would
    show up as literal text. This second pass re-coerces only the '=' cells to
    parsed formulas in one batched call, leaving every value string untouched."""
    formula_cells = [
        {'range': f'{_a1_col(col)}{row_number}', 'values': [[value]]}
        for row_number, row in enumerate(rows, start=1)
        for col, value in enumerate(row, start=1)
        if isinstance(value, str) and value.startswith('=')
    ]
    if not formula_cells:
        return
    _sheets_call(
        f'reapply {len(formula_cells)} formula cells {worksheet.title}',
        # Fresh dicts on every attempt: gspread's Worksheet.batch_update
        # rewrites each entry's 'range' IN PLACE to "'<title>'!<range>"
        # before posting, so retrying the same list after a quota hit would
        # prefix the title a second time ("'HH'!'HH'!C7" -> 400 Unable to
        # parse range) and turn a recoverable 429 into a hard failure.
        lambda: worksheet.batch_update(
            [dict(cell) for cell in formula_cells],
            value_input_option='USER_ENTERED',
        ),
    )


def _replace_draft_tab(spreadsheet, rows, color_grid=None):
    """Clear/create the Draft Recap tab and write it. Returns the worksheet
    so the two-pass write can read its gid for the Home nav link.

    color_grid (from build_draft_board_color_grid) drives the per-cell
    red->white->green color scale on the board; None skips it."""
    width = max((len(row) for row in rows), default=20)
    try:
        worksheet = spreadsheet.worksheet(DRAFT_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {DRAFT_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=DRAFT_TAB, rows=max(len(rows) + 10, 50), cols=max(width, 20),
            ),
        )

    _sheets_call(f'clear {DRAFT_TAB}', worksheet.clear)
    # RAW so the signed value strings ("+253") keep their '+' -- USER_ENTERED
    # would coerce them to plain numbers and drop the sign.
    _sheets_call(
        f'update {DRAFT_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )
    _reapply_formula_cells(worksheet, rows)

    try:
        last_col = _a1_col(width)
        _apply_draft_tab_dimensions(spreadsheet, worksheet, width)
        formats = [
            {'range': 'A1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
            {'range': f'A2:{last_col}2', 'format': {'textFormat': {'italic': True}}},
        ]
        formats.extend(_draft_label_formats(rows, last_col))
        _batch_format(worksheet, formats)
        if color_grid:
            _apply_draft_board_colors(spreadsheet, worksheet, rows, color_grid)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {DRAFT_TAB}: {exc}")

    return worksheet


def _draft_label_formats(rows, last_col):
    """Bold the Draft Recap section labels (side-by-side leaderboards + the
    board), the leaderboard column headers, and the board header row."""
    formats = []
    for row_number, row in enumerate(rows, 1):
        first = row[0] if row else ''
        seventh = row[6] if len(row) > 6 else ''
        if first == 'Best Value Picks':
            formats.append({
                'range': f'A{row_number}:E{row_number}',
                'format': {'textFormat': {'bold': True, 'fontSize': 12}},
            })
        if seventh == 'Biggest Busts':
            formats.append({
                'range': f'G{row_number}:K{row_number}',
                'format': {'textFormat': {'bold': True, 'fontSize': 12}},
            })
        if isinstance(first, str) and first.startswith('Draft Board'):
            formats.append({
                'range': f'A{row_number}:{last_col}{row_number}',
                'format': {'textFormat': {'bold': True, 'fontSize': 12}},
            })
        if first == 'Player' and len(row) > 1 and row[1] == 'Team':
            for cell_range in (f'A{row_number}:E{row_number}', f'G{row_number}:K{row_number}'):
                formats.append({
                    'range': cell_range,
                    'format': {
                        'textFormat': {'bold': True},
                        'backgroundColor': _DRAFT_HEADER_BG,
                    },
                })
        if first == 'Rd':
            formats.append({
                'range': f'A{row_number}:{last_col}{row_number}',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': _DRAFT_BOARD_HEADER_BG,
                },
            })
    return formats


def _apply_draft_tab_dimensions(spreadsheet, worksheet, width):
    """Col A (Player / Rd) wide; Min/Median/Max + leaderboard meta narrow;
    team + Value columns sized for player names."""
    sheet_id = worksheet.id
    requests = [
        _column_width_request(sheet_id, 0, 1, 120),
        _column_width_request(sheet_id, 1, 4, 70),
        _column_width_request(sheet_id, 4, max(width, 5), 95),
    ]
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_draft_board_colors(spreadsheet, worksheet, rows, color_grid):
    """Red->white->green per-cell color scale on the board, by player season
    points. Text cells can't use Sheets' numeric gradient rule, so set the
    backgrounds directly in one updateCells request (a backgroundColor-only
    field mask preserves the player-name values)."""
    rd_index = next(
        (i for i, row in enumerate(rows) if row and row[0] == 'Rd'), None)
    if rd_index is None:
        return
    all_points = [p for row in color_grid for p in row if p is not None]
    if not all_points:
        return
    low, high = min(all_points), max(all_points)
    team_count = max((len(row) for row in color_grid), default=0)
    if not team_count:
        return

    cell_rows = []
    for row in color_grid:
        values = []
        for col in range(team_count):
            points = row[col] if col < len(row) else None
            color = (_draft_gradient_color(points, low, high)
                     if points is not None
                     else {'red': 1, 'green': 1, 'blue': 1})
            values.append({'userEnteredFormat': {'backgroundColor': color}})
        cell_rows.append({'values': values})

    start_row = rd_index + 1  # board data begins the row after the Rd header
    request = {
        'updateCells': {
            'range': {
                'sheetId': worksheet.id,
                'startRowIndex': start_row,
                'endRowIndex': start_row + len(color_grid),
                'startColumnIndex': _DRAFT_BOARD_TEAM_START,
                'endColumnIndex': _DRAFT_BOARD_TEAM_START + team_count,
            },
            'rows': cell_rows,
            'fields': 'userEnteredFormat.backgroundColor',
        },
    }
    _sheets_batch_update(spreadsheet, f'draft board colors {worksheet.title}', [request])


def _draft_gradient_color(value, low, high):
    """Map a season-points value to a red (low) -> white (mid) -> green (high)
    background, matching the team-weeks palette."""
    if high <= low:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (value - low) / (high - low)))
    red = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
    white = {'red': 1.0, 'green': 1.0, 'blue': 1.0}
    green = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
    if t < 0.5:
        return _lerp_color(red, white, t / 0.5)
    return _lerp_color(white, green, (t - 0.5) / 0.5)


def _lerp_color(c1, c2, t):
    return {channel: c1[channel] + (c2[channel] - c1[channel]) * t
            for channel in ('red', 'green', 'blue')}


def _standings_table_bounds(rows):
    """Locate the two stacked tables on the Advanced Standings tab. Returns
    (a_header, a_end, b_header, b_end) as 0-based row indices: the Standings
    header row and the slot header row, each with the exclusive end of the data
    block beneath it. None for a table that isn't found.

    Table B is indented one cell (its Team / Owner columns sit under Table
    A's), so its header and data rows key off column 1, not column 0."""
    a_hdr = next((i for i, r in enumerate(rows)
                  if r and r[0] == 'Rank' and 'Offense' in r), None)
    b_hdr = next((i for i, r in enumerate(rows)
                  if a_hdr is not None and i > a_hdr
                  and len(r) > 1 and r[0] == '' and r[1] == 'Team'), None)

    def _data_end(start, col=0):
        end = start
        while (end < len(rows) and rows[end] and len(rows[end]) > col
               and rows[end][col] not in ('', None)):
            end += 1
        return end

    a_end = _data_end(a_hdr + 1) if a_hdr is not None else None
    b_end = _data_end(b_hdr + 1, col=1) if b_hdr is not None else None
    return a_hdr, a_end, b_hdr, b_end


def _apply_standings_gradients(spreadsheet, worksheet, rows, stat_specs):
    """Red->white->green column gradients. Table A: every stat and points
    column, polarity-aware -- positive-weighted stats and the three score
    totals paint green-high, negative-weighted stats (L / ER / BLSV / ...)
    and Against paint green-low, zero-weighted stats get no gradient.
    Column positions come from standings_gradient_columns (positional, not
    label lookup -- K / BB / H / HR / R appear in both stat blocks). Table
    B: every lineup-slot column, green-high."""
    sheet_id = worksheet.id
    a_hdr, a_end, b_hdr, b_end = _standings_table_bounds(rows)
    requests = []
    if a_hdr is not None:
        a_range = [{'startRowIndex': a_hdr + 1, 'endRowIndex': a_end}]
        gradient_columns = almanac_render.standings_gradient_columns(
            _team_week_specs_for_category(stat_specs, 'hitting'),
            _team_week_specs_for_category(stat_specs, 'pitching'),
        )
        for col, direction in gradient_columns:
            if direction is None:
                continue
            scale = 'three_good_high' if direction == 'most' else 'three_good_low'
            requests.append(_color_scale_request(
                sheet_id, col, a_end, scale=scale, row_ranges=a_range,
            ))
    if b_hdr is not None:
        b_range = [{'startRowIndex': b_hdr + 1, 'endRowIndex': b_end}]
        # Slot values start after the indent + Team + Owner cells.
        for col in range(3, len(rows[b_hdr])):
            requests.append(_color_scale_request(
                sheet_id, col, b_end, scale='three_good_high', row_ranges=b_range,
            ))
    if requests:
        _sheets_batch_update(
            spreadsheet, f'standings gradients {worksheet.title}', requests,
        )


def _replace_advanced_standings_tab(spreadsheet, rows, stat_specs):
    """Clear / create the Advanced Standings tab and write it. Returns the
    worksheet so write_almanac can read its gid for the Home nav band.

    Written RAW so the W-L record strings ("11-2") aren't parsed as dates; the
    numeric point cells stay numeric, so the column gradients still apply."""
    width = max((len(row) for row in rows), default=20)
    try:
        worksheet = spreadsheet.worksheet(ADVANCED_STANDINGS_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {ADVANCED_STANDINGS_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=ADVANCED_STANDINGS_TAB,
                rows=max(len(rows) + 10, 50),
                cols=max(width, 20),
            ),
        )

    _sheets_call(f'clear {ADVANCED_STANDINGS_TAB}', worksheet.clear)
    _sheets_call(
        f'update {ADVANCED_STANDINGS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )

    try:
        sheet_id = worksheet.id
        last_col = _a1_col(width)
        a_hdr, _, b_hdr, _ = _standings_table_bounds(rows)
        _apply_standings_gradients(spreadsheet, worksheet, rows, stat_specs)
        # Widths by column TYPE, derived from the Table A header layout
        # rather than hardcoded letters, so other leagues' stat counts get
        # the right shape: identity columns wide, value columns narrow,
        # buffers narrower still. The blanket 40px request lands first;
        # the per-buffer 25px overrides follow (later requests win within
        # one batchUpdate). None of these touch hiddenByUser, so manually
        # hidden columns (e.g. a stat the league never records) stay
        # hidden across reruns.
        width_requests = [
            _column_width_request(sheet_id, 0, 2, 52),      # Rank, Team
            _column_width_request(sheet_id, 2, 3, 125),     # Owner
            _column_width_request(sheet_id, 3, width, 40),  # W-L + every value column
        ]
        if a_hdr is not None:
            width_requests.extend(
                _column_width_request(sheet_id, col, col + 1, 25)
                for col, cell in enumerate(rows[a_hdr])
                if col >= 4 and cell == ''
            )
        _sheets_batch_update(
            spreadsheet, f'standings widths {worksheet.title}', width_requests,
        )
        formats = [
            {'range': 'A1', 'format': {'textFormat': {'bold': True, 'fontSize': 13}}},
        ]
        for header_idx in (a_hdr, b_hdr):
            if header_idx is None:
                continue
            r = header_idx + 1
            formats.append({
                'range': f'A{r}:{last_col}{r}',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            })
            if header_idx >= 1:  # bold the section label one row above
                formats.append({
                    'range': f'A{header_idx}',
                    'format': {'textFormat': {'bold': True}},
                })
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] standings formatting skipped: {exc}")

    return worksheet


def _replace_team_weeks_tab(spreadsheet, rows, stat_specs, source_rows=None, record_marks=None):
    """Recreate and write the team-week matchup archive tab."""
    try:
        worksheet = spreadsheet.worksheet(TEAM_WEEKS_TAB)
        _sheets_call(
            f'delete {TEAM_WEEKS_TAB}',
            lambda: spreadsheet.del_worksheet(worksheet),
        )
    except gspread.WorksheetNotFound:
        pass

    worksheet = _sheets_call(
        f'create {TEAM_WEEKS_TAB}',
        lambda: spreadsheet.add_worksheet(
            title=TEAM_WEEKS_TAB,
            rows=max(len(rows) + 10, 50),
            cols=max(len(rows[0]) if rows else 1, 20),
        ),
    )
    _sheets_call(
        f'update {TEAM_WEEKS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    try:
        _sheets_call(f'freeze {TEAM_WEEKS_TAB}', lambda: worksheet.freeze(rows=1))
        _apply_team_weeks_tab_dimensions(spreadsheet, worksheet, rows, stat_specs)
        _apply_team_weeks_conditional_formats(
            spreadsheet,
            worksheet,
            rows,
            stat_specs,
            source_rows=source_rows,
        )
        _apply_team_weeks_record_formats(
            spreadsheet,
            worksheet,
            stat_specs,
            source_rows=source_rows,
            record_marks=record_marks,
        )
        layout = _team_weeks_layout(stat_specs)
        score_start_col = _a1_col(layout['score_start'] + 1)
        score_end_col = _a1_col(layout['score_start'] + 4)
        matchup_start_col = _a1_col(layout['score_start'] + 8)
        matchup_end_col = _a1_col(layout['score_start'] + 10)
        avg_start_col = _a1_col(layout['score_start'] + 12)
        avg_end_col = _a1_col(layout['score_start'] + 14)
        _batch_format(worksheet, [
            {
                'range': f'A1:{_a1_col(len(rows[0]))}1',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            },
            {
                'range': f'A:{_a1_col(len(rows[0]))}',
                'format': {'wrapStrategy': 'OVERFLOW_CELL'},
            },
            {
                'range': f'{score_start_col}:{score_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': f'{matchup_start_col}:{matchup_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': f'{avg_start_col}:{avg_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
        ])
    except Exception as exc:
        print(f"[almanac] formatting skipped for {TEAM_WEEKS_TAB}: {exc}")

    return worksheet


def _replace_records_tab(spreadsheet, rows):
    """Clear/create Records and write the curated record-book tab."""
    try:
        worksheet = spreadsheet.worksheet(RECORDS_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {RECORDS_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=RECORDS_TAB,
                rows=max(len(rows) + 10, 50),
                cols=RECORDS_MATRIX_WIDTH,
            ),
        )

    _sheets_call(f'clear {RECORDS_TAB}', worksheet.clear)
    _sheets_call(
        f'update {RECORDS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    try:
        _sheets_call(f'freeze {RECORDS_TAB}', lambda: worksheet.freeze(rows=3))
        _apply_records_tab_dimensions(spreadsheet, worksheet)
        _merge_records_scope_headers(spreadsheet, worksheet, rows)
        formats = [
            {
                'range': 'A:L',
                'format': {
                    'textFormat': {'bold': False, 'italic': False},
                    'backgroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    'wrapStrategy': 'OVERFLOW_CELL',
                },
            },
            {
                'range': 'A1:L1',
                'format': {'textFormat': {'bold': True, 'fontSize': 14}},
            },
            {
                'range': 'A2:L2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
            {
                # Row 3: formatting legend (italic + small, matching the note row).
                'range': 'A3:L3',
                'format': {
                    'textFormat': {'italic': True, 'fontSize': 9},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
        ]
        formats.extend(_records_header_formats(rows))
        formats.extend(_fresh_record_formats(rows))
        formats.extend(_records_score_value_formats(rows))
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {RECORDS_TAB}: {exc}")

    return worksheet


def _records_header_formats(rows):
    """Return batched formats for section labels and matrix headers."""
    formats = []
    for row_number, row in enumerate(rows, 1):
        if _is_records_scope_header(row) or row == RECORDS_MATRIX_DETAIL_HEADER:
            formats.append({
                'range': f'A{row_number}:L{row_number}',
                'format': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            })
    return formats


def _is_records_scope_header(row):
    return (
        len(row) == RECORDS_MATRIX_WIDTH
        and row[1] == 'Current Season'
        and row[7] == 'All-Time'
    )


# Burnt orange / gold background for a brand-new ALL-TIME record set in the
# MOST RECENT week -- the strongest "you just made league history this week"
# cue, layered over the italic this-season marker. Tunable.
_FRESH_ALL_TIME_RECORD_BG = {'red': 0.92, 'green': 0.60, 'blue': 0.20}


def _fresh_record_formats(rows):
    """Return batched formats for fresh current-season/all-time records."""
    formats = []
    season_year, matchup_period = get_latest_matchup_period()
    schedule_lookup = records.load_schedule_lookup()
    latest_period = records.format_week_label(season_year, matchup_period, schedule_lookup)

    for row_number, row in enumerate(rows, 1):
        if len(row) < RECORDS_MATRIX_WIDTH:
            continue
        current_period = row[4]
        all_time_period = row[10]
        if (
            isinstance(current_period, str)
            and latest_period in current_period
            and _record_side_is_small_tie(row[1])
        ):
            formats.append({
                'range': f'A{row_number}:F{row_number}',
                'format': {'textFormat': {'italic': True}},
            })
        if (
            isinstance(all_time_period, str)
            and f': {season_year}' in all_time_period
            and _record_side_is_small_tie(row[7])
        ):
            formats.append({
                'range': f'H{row_number}:L{row_number}',
                'format': {'textFormat': {'italic': True}},
            })
        # v1.3: a brand-new all-time record set in the MOST RECENT week (not
        # just somewhere this season) gets a burnt-orange/gold background --
        # the "you just set a league all-time record this week" highlight.
        # latest_period already carries the ": {season}" suffix via the cell,
        # so match the full "<week>: <season>" to avoid week-substring hits
        # (e.g. "Week 1" inside "Week 11").
        if (
            isinstance(all_time_period, str)
            and f'{latest_period}: {season_year}' in all_time_period
            and _record_side_is_small_tie(row[7])
        ):
            formats.append({
                'range': f'H{row_number}:L{row_number}',
                'format': {'backgroundColor': _FRESH_ALL_TIME_RECORD_BG},
            })
            # A fresh all-time record IS also the current-season record, so it
            # appears on the left panel too -- mirror the highlight onto A:F.
            # (Its italic already fires via the current-season branch above.)
            if _record_side_is_small_tie(row[1]):
                formats.append({
                    'range': f'A{row_number}:F{row_number}',
                    'format': {'backgroundColor': _FRESH_ALL_TIME_RECORD_BG},
                })
    return formats


def _record_side_is_small_tie(holder):
    """Return true when a side is a single holder or a listed tie of 3 or fewer."""
    holder_text = str(holder or '').strip()
    if not holder_text:
        return False
    match = re.match(r'^(\d+)\s+\w+\s+tied$', holder_text)
    if match:
        return int(match.group(1)) < 4
    return True


def _records_score_value_formats(rows):
    """Force score/points values to one decimal without affecting count stats."""
    formats = []
    active_section = ''
    for row_number, row in enumerate(rows, 1):
        if _is_records_scope_header(row):
            active_section = row[0]
            continue
        if len(row) < RECORDS_MATRIX_WIDTH:
            continue
        if active_section == 'Score Records' or active_section == 'Lineup Slot Records':
            formats.extend([
                {
                    'range': f'D{row_number}:D{row_number}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
                },
                {
                    'range': f'J{row_number}:J{row_number}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
                },
            ])
    return formats


def _replace_team_tab(spreadsheet, title, rows):
    """Clear/create one fantasy team roster tab and write rows."""
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {title}',
            lambda: spreadsheet.add_worksheet(
                title=title,
                rows=max(len(rows) + 10, 50),
                cols=TEAM_ROSTER_MATRIX_WIDTH,
            ),
        )

    _sheets_call(f'clear {title}', worksheet.clear)
    _sheets_call(
        f'update {title}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )
    _reapply_formula_cells(worksheet, rows)

    try:
        _sheets_call(f'freeze {title}', lambda: worksheet.freeze(rows=5))
        _apply_team_tab_dimensions(spreadsheet, worksheet)
        formats = [
            {
                'range': 'A1:AC1',
                'format': {'textFormat': {'bold': True, 'fontSize': 13}},
            },
            {
                'range': 'A2:AC2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
            {
                'range': 'A4:AC5',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            },
            {
                # Points glossary (Q1:Q3) -- plain, left-aligned, small.
                # Comes after the A1/A2 row formats so it overrides their
                # bold/italic on these cells.
                'range': 'Q1:Q3',
                'format': {
                    'horizontalAlignment': 'LEFT',
                    'textFormat': {'bold': False, 'italic': False, 'fontSize': 10},
                },
            },
            {
                'range': 'A5:A',
                'format': {'textFormat': {'fontSize': 5}},
            },
            {
                'range': 'P5:P',
                'format': {'textFormat': {'fontSize': 5}},
            },
            {
                # Wrap only the row-5 stat headers (the multi-word Roster Days /
                # Games / Active Points labels) so they stack in the 50px
                # columns; the short numeric data below stays single-line.
                'range': 'E5:G5',
                'format': {'wrapStrategy': 'WRAP'},
            },
            {
                'range': 'T5:V5',
                'format': {'wrapStrategy': 'WRAP'},
            },
            {
                'range': 'G:H',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
            {
                'range': 'V:W',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
            {
                'range': 'I:I',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': 'X:X',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': 'E:F',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
            {
                'range': 'T:U',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
        ]
        for row_number, row in enumerate(rows, 1):
            if len(row) > 9 and row[9] in {'Avg', 'W-L (Sv)', 'Avg|W-L-Sv'}:
                text_format = {'bold': True}
                if row_number <= 5:
                    text_format['foregroundColor'] = {'red': 1, 'green': 1, 'blue': 1}
                formats.extend([
                    {
                        'range': f'J{row_number}:N{row_number}',
                        'format': {'textFormat': text_format},
                    },
                    {
                        'range': f'Y{row_number}:AC{row_number}',
                        'format': {'textFormat': text_format},
                    },
                ])
            # Header rows (team name / subtitle / glossary / scope / column
            # header) carry labels + glossary text in cols B/Q, not slot codes
            # -- skip the per-row data-cell alignment so the left-aligned
            # glossary (Q1:Q3) is not clobbered by a spurious RIGHT.
            if row_number <= 5:
                continue
            if _is_active_display_slot(row[1] if len(row) > 1 else ''):
                formats.append({
                    'range': f'B{row_number}:B{row_number}',
                    'format': {'horizontalAlignment': 'RIGHT'},
                })
            if _is_active_display_slot(row[16] if len(row) > 16 else ''):
                formats.append({
                    'range': f'Q{row_number}:Q{row_number}',
                    'format': {'horizontalAlignment': 'RIGHT'},
                })
            if _is_pitcher_display_slot(row[1] if len(row) > 1 else ''):
                formats.extend([
                    {
                        'range': f'J{row_number}:N{row_number}',
                        'format': {'horizontalAlignment': 'LEFT'},
                    },
                ])
            elif _is_hitter_display_slot(row[1] if len(row) > 1 else ''):
                formats.extend([
                    {
                        'range': f'J{row_number}:N{row_number}',
                        'format': {'horizontalAlignment': 'RIGHT'},
                    },
                ])
            if _is_pitcher_display_slot(row[16] if len(row) > 16 else ''):
                formats.extend([
                    {
                        'range': f'Y{row_number}:AC{row_number}',
                        'format': {'horizontalAlignment': 'LEFT'},
                    },
                ])
            elif _is_hitter_display_slot(row[16] if len(row) > 16 else ''):
                formats.extend([
                    {
                        'range': f'Y{row_number}:AC{row_number}',
                        'format': {'horizontalAlignment': 'RIGHT'},
                    },
                ])
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {title}: {exc}")

    return worksheet


def _apply_records_tab_dimensions(spreadsheet, worksheet):
    sheet_id = worksheet.id
    requests = [
        _column_width_request(sheet_id, 0, 1, 175),
        _column_width_request(sheet_id, 1, 2, 150),   # B: Holder
        _column_width_request(sheet_id, 2, 3, 125),   # C: Owner
        _column_width_request(sheet_id, 5, 6, 400),
        _column_width_request(sheet_id, 6, 7, 25),     # G: buffer between panels
        _column_width_request(sheet_id, 11, 12, 400),
    ]
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_team_weeks_tab_dimensions(spreadsheet, worksheet, rows, stat_specs):
    sheet_id = worksheet.id
    layout = _team_weeks_layout(stat_specs)
    total_cols = len(rows[0]) if rows else 1
    requests = [
        _column_width_request(sheet_id, 0, 1, 70),
        _hidden_columns_request(sheet_id, 0, 1, hidden=True),
        _column_width_request(sheet_id, 1, 2, 70),
        _column_width_request(sheet_id, 2, 3, 90),
        _column_width_request(sheet_id, 3, 4, 175),
        _column_width_request(sheet_id, layout['hitting_start'], layout['hitting_end'], 42),
        _column_width_request(sheet_id, layout['hitting_spacer'], layout['hitting_spacer'] + 1, 12),
        _column_width_request(sheet_id, layout['pitching_start'], layout['pitching_end'], 42),
        _column_width_request(sheet_id, layout['pitching_spacer'], layout['pitching_spacer'] + 1, 12),
        _column_width_request(sheet_id, layout['score_start'], layout['score_start'] + 4, 85),
        _column_width_request(sheet_id, layout['score_start'] + 4, layout['score_start'] + 6, 38),
        _column_width_request(sheet_id, layout['score_start'] + 6, layout['score_start'] + 7, 32),
        _column_width_request(sheet_id, layout['score_start'] + 7, layout['score_start'] + 10, 95),
        _column_width_request(sheet_id, layout['score_start'] + 10, layout['score_start'] + 11, 32),
        _column_width_request(sheet_id, layout['score_start'] + 11, total_cols, 90),
    ]
    for column in _team_weeks_rare_column_indices(stat_specs):
        requests.append(_hidden_columns_request(sheet_id, column, column + 1, hidden=True))
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_team_weeks_conditional_formats(spreadsheet, worksheet, rows, stat_specs,
                                          source_rows=None):
    if len(rows) <= 1:
        return
    sheet_id = worksheet.id
    layout = _team_weeks_layout(stat_specs)
    row_ranges = _team_weeks_standard_data_ranges(sheet_id, len(rows), source_rows)
    requests = []

    for column, spec in _team_weeks_stat_column_pairs(stat_specs):
        stat_name = spec.get('stat_name')
        if stat_name in TEAM_WEEKS_WHITE_TO_GREEN_STATS:
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale='two_good', row_ranges=row_ranges,
            ))
        elif stat_name in TEAM_WEEKS_WHITE_TO_RED_STATS:
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale='two_bad', row_ranges=row_ranges,
            ))
        else:
            scale = 'three_good_high'
            if (spec.get('points_per_unit') or 0) < 0:
                scale = 'three_good_low'
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale=scale, row_ranges=row_ranges,
            ))

    for column in [
        layout['score_start'],
        layout['score_start'] + 1,
        layout['score_start'] + 2,
        layout['score_start'] + 3,
        layout['score_start'] + 7,
        layout['score_start'] + 8,
        layout['score_start'] + 9,
        layout['score_start'] + 11,
        layout['score_start'] + 12,
        layout['score_start'] + 13,
    ]:
        requests.append(_color_scale_request(
            sheet_id, column, len(rows), scale='three_good_high', row_ranges=row_ranges,
        ))

    _sheets_batch_update(spreadsheet, f'conditional formats {worksheet.title}', requests)


def _apply_team_weeks_record_formats(spreadsheet, worksheet, stat_specs,
                                     source_rows=None, record_marks=None):
    if not source_rows or not record_marks:
        return
    sheet_id = worksheet.id
    requests = []
    for column_index, spec in _team_weeks_stat_column_pairs(stat_specs):
        mark = record_marks.get(spec.get('stat_name'))
        if not mark or _is_zeroish(mark.get('value')):
            continue
        for source_index, source_row in enumerate(source_rows):
            if source_row.get('is_abnormal'):
                continue
            if not _numeric_values_equal(source_row.get(_fact_stat_column_name(spec.get('stat_name'))), mark.get('value')):
                continue
            text_format = {'bold': True}
            if int(mark.get('holder_count') or 0) == 1:
                text_format['foregroundColor'] = {'red': 0.72, 'green': 0.48, 'blue': 0.00}
            requests.append(_cell_format_request(
                sheet_id,
                row_index=source_index + 1,
                column_index=column_index,
                cell_format={'textFormat': text_format},
            ))
    _sheets_batch_update(spreadsheet, f'record formats {worksheet.title}', requests)


def _team_weeks_standard_data_ranges(sheet_id, row_count, source_rows=None):
    """Return contiguous non-abnormal data-row ranges for conditional formatting."""
    if not source_rows:
        return [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': row_count}]

    ranges = []
    start = None
    for source_index, source_row in enumerate(source_rows):
        row_index = source_index + 1
        is_standard = not source_row.get('is_abnormal')
        if is_standard and start is None:
            start = row_index
        elif not is_standard and start is not None:
            ranges.append({'sheetId': sheet_id, 'startRowIndex': start, 'endRowIndex': row_index})
            start = None
    if start is not None:
        ranges.append({
            'sheetId': sheet_id,
            'startRowIndex': start,
            'endRowIndex': min(len(source_rows) + 1, row_count),
        })
    return ranges


def _numeric_values_equal(left, right):
    if left is None or right is None:
        return False
    try:
        return math.isclose(float(left), float(right), rel_tol=0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _is_zeroish(value):
    if value is None:
        return False
    try:
        return math.isclose(float(value), 0.0, rel_tol=0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return False


def _team_weeks_layout(stat_specs):
    hitting_count = len(_team_week_specs_for_category(stat_specs, 'hitting'))
    pitching_count = len(_team_week_specs_for_category(stat_specs, 'pitching'))
    hitting_start = len(TEAM_WEEKS_BASE_HEADER)
    hitting_end = hitting_start + hitting_count
    hitting_spacer = hitting_end
    pitching_start = hitting_spacer + 1
    pitching_end = pitching_start + pitching_count
    pitching_spacer = pitching_end
    score_start = pitching_spacer + 1
    return {
        'hitting_start': hitting_start,
        'hitting_end': hitting_end,
        'hitting_spacer': hitting_spacer,
        'pitching_start': pitching_start,
        'pitching_end': pitching_end,
        'pitching_spacer': pitching_spacer,
        'score_start': score_start,
    }


def _team_weeks_stat_column_pairs(stat_specs):
    layout = _team_weeks_layout(stat_specs)
    pairs = []
    hitting_specs = _team_week_specs_for_category(stat_specs, 'hitting')
    pitching_specs = _team_week_specs_for_category(stat_specs, 'pitching')
    pairs.extend((layout['hitting_start'] + index, spec) for index, spec in enumerate(hitting_specs))
    pairs.extend((layout['pitching_start'] + index, spec) for index, spec in enumerate(pitching_specs))
    return pairs


def _team_weeks_rare_column_indices(stat_specs):
    return [
        column
        for column, spec in _team_weeks_stat_column_pairs(stat_specs)
        if _is_rare_team_week_stat(spec.get('stat_name'))
    ]


def _merge_records_scope_headers(spreadsheet, worksheet, rows):
    sheet_id = worksheet.id
    requests = []
    for row_index, row in enumerate(rows):
        if not _is_records_scope_header(row):
            continue
        for start_col, end_col in [(1, 6), (7, 12)]:
            grid_range = {
                'sheetId': sheet_id,
                'startRowIndex': row_index,
                'endRowIndex': row_index + 1,
                'startColumnIndex': start_col,
                'endColumnIndex': end_col,
            }
            requests.append({'unmergeCells': {'range': grid_range}})
            requests.append({'mergeCells': {'range': grid_range, 'mergeType': 'MERGE_ALL'}})
    _sheets_batch_update(spreadsheet, f'merge scope headers {worksheet.title}', requests)


def _delete_prefixed_team_tabs(spreadsheet, current_titles):
    """Remove obsolete team tabs from the old T## naming scheme."""
    for worksheet in spreadsheet.worksheets():
        title = worksheet.title
        if title in current_titles:
            continue
        if re.match(r'^T\d{2}\s+', title):
            _sheets_call(
                f'delete old team tab {title}',
                lambda worksheet=worksheet: spreadsheet.del_worksheet(worksheet),
            )


def _sort_almanac_tabs(spreadsheet, ordered_titles):
    worksheets_by_title = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
    requests = []
    for index, title in enumerate(ordered_titles):
        worksheet = worksheets_by_title.get(title)
        if not worksheet:
            continue
        requests.append({
            'updateSheetProperties': {
                'properties': {
                    'sheetId': worksheet.id,
                    'index': index,
                },
                'fields': 'index',
            },
        })
    _sheets_batch_update(spreadsheet, 'sort almanac tabs', requests)


def _apply_team_tab_dimensions(spreadsheet, worksheet):
    sheet_id = worksheet.id
    requests = []
    for start_index, width in [
        (0, 25),    # Tm
        (1, 75),    # Slot
        (3, 40),    # Team
        (4, 50),    # RosterDays
        (5, 50),    # Games
        (6, 50),    # Active Points
        (7, 75),    # Bench/IL Points
        (8, 40),    # ppg
        (14, 15),   # spacer
        (15, 25),   # Tm
        (16, 75),   # Slot
        (18, 40),   # Team
        (19, 50),   # RosterDays
        (20, 50),   # Games
        (21, 50),   # Active Points
        (22, 75),   # Bench/IL Points
        (23, 40),   # ppg
    ]:
        requests.append(_column_width_request(sheet_id, start_index, start_index + 1, width))
    requests.extend([
        _column_width_request(sheet_id, 9, 14, 80),
        _column_width_request(sheet_id, 24, 29, 80),
        _auto_resize_columns_request(sheet_id, 2, 3),
        _auto_resize_columns_request(sheet_id, 17, 18),
    ])
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _column_width_request(sheet_id, start_index, end_index, pixel_size):
    return {
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
            'properties': {'pixelSize': pixel_size},
            'fields': 'pixelSize',
        },
    }


def _hidden_columns_request(sheet_id, start_index, end_index, hidden=True):
    return {
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
            'properties': {'hiddenByUser': hidden},
            'fields': 'hiddenByUser',
        },
    }


def _color_scale_request(sheet_id, column_index, row_count, scale='three_good_high',
                         row_ranges=None):
    if scale in {'two_good', 'two_bad'}:
        max_color = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
        if scale == 'two_bad':
            max_color = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
        gradient_rule = {
            'minpoint': {
                'type': 'NUMBER',
                'value': '0',
                'color': {'red': 1, 'green': 1, 'blue': 1},
            },
            'maxpoint': {
                'type': 'MAX',
                'color': max_color,
            },
        }
    else:
        low_color = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
        high_color = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
        if scale == 'three_good_low':
            low_color, high_color = high_color, low_color
        gradient_rule = {
            'minpoint': {'type': 'MIN', 'color': low_color},
            'midpoint': {
                'type': 'PERCENTILE',
                'value': '50',
                'color': {'red': 1, 'green': 1, 'blue': 1},
            },
            'maxpoint': {'type': 'MAX', 'color': high_color},
        }

    return {
        'addConditionalFormatRule': {
            'rule': {
                'ranges': _color_scale_ranges(
                    sheet_id,
                    column_index,
                    row_count,
                    row_ranges=row_ranges,
                ),
                'gradientRule': gradient_rule,
            },
            'index': 0,
        },
    }


def _color_scale_ranges(sheet_id, column_index, row_count, row_ranges=None):
    ranges = row_ranges or [{'startRowIndex': 1, 'endRowIndex': row_count}]
    return [
        {
            'sheetId': sheet_id,
            'startRowIndex': row_range['startRowIndex'],
            'endRowIndex': row_range['endRowIndex'],
            'startColumnIndex': column_index,
            'endColumnIndex': column_index + 1,
        }
        for row_range in ranges
        if row_range['startRowIndex'] < row_range['endRowIndex']
    ]


def _cell_format_request(sheet_id, row_index, column_index, cell_format):
    return {
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': row_index,
                'endRowIndex': row_index + 1,
                'startColumnIndex': column_index,
                'endColumnIndex': column_index + 1,
            },
            'cell': {'userEnteredFormat': cell_format},
            'fields': 'userEnteredFormat.textFormat',
        },
    }


def _auto_resize_columns_request(sheet_id, start_index, end_index):
    return {
        'autoResizeDimensions': {
            'dimensions': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
        },
    }


def _a1_col(index_1_based):
    letters = ''
    index = index_1_based
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _batch_format(worksheet, formats):
    if not formats:
        return
    _sheets_call(
        f'format {worksheet.title}',
        lambda: worksheet.batch_format(formats),
    )


def _sheets_batch_update(spreadsheet, label, requests):
    if not requests:
        return
    _sheets_call(label, lambda: spreadsheet.batch_update({'requests': requests}))


def _sheets_call(label, func, attempts=3, delay_seconds=70):
    """Run a Sheets mutation, backing off when the API write quota resets."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except gspread.exceptions.APIError as exc:
            if attempt == attempts or not _is_quota_error(exc):
                raise
            print(
                f"[almanac] Sheets quota hit during {label}; "
                f"retrying in {delay_seconds}s"
            )
            time.sleep(delay_seconds)


def _is_quota_error(exc):
    message = str(exc).lower()
    return '[429]' in message or 'quota exceeded' in message or 'rate limit' in message
