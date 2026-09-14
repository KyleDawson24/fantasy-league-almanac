"""Sheets writer for the redesigned Records tabs (MLB-212 dev render).

One writer for both books. It knows the eighteen-column geometry the
presenter emits and nothing about either league: it creates or replaces
the three tabs, writes values USER_ENTERED (so the =HYPERLINK cells
parse), paints the presenter's format spec, adds the collapsible row
groups, freezes the title band, and resolves the jump index into in-sheet
links once the tab's gid exists.

Tab placement (spec section 1, 09-09; ordering revised 09-14): the three
tabs ride directly after Advanced Standings, ordered Lifetime · Season ·
Matchup, and the standing Records tab is hidden -- never deleted, so the
book keeps the old page for comparison. That is the same slot the
standing renderers' sort passes give them (almanac_write
.with_records_book_tabs), so the two writers no longer take turns moving
the strip. A book with no Advanced Standings tab falls back to the
standing Records tab's own position. Prod comes through here from Kyle's
09-11 ruling on.
"""

import gspread

from almanac_render import ADVANCED_STANDINGS_TAB
from almanac_write import _is_quota_error, _sheets_call
from records_book_logic import BAND_COLS, BAND_STARTS, TITLES, WIDTH, _col, sheet_safe

PLACEMENT_ORDER = (TITLES['lifetime'], TITLES['season'], TITLES['matchup'])
from sheets_writer import _get_authorized_client

# Pixel widths per column, the mockup's: A label, then Holder / Owner /
# Value / Details / Period per band, with 18px spacers.
_BAND_WIDTHS = [150, 110, 78, 300, 110]
_WIDTHS = [170] + _BAND_WIDTHS + [18] + _BAND_WIDTHS + [18] + _BAND_WIDTHS


def _grid(sheet_id, a1):
    return gspread.utils.a1_range_to_grid_range(a1, sheet_id=sheet_id)


def _style_requests(sheet_id, sheet, n_rows):
    requests = [
        {'repeatCell': {'range': {'sheetId': sheet_id}, 'cell': {},
                        'fields': 'userEnteredFormat'}},
        {'updateSheetProperties': {
            # Rows only: the banners merge across column A, and Sheets
            # refuses a merge that spans a frozen column boundary.
            'properties': {'sheetId': sheet_id,
                           'gridProperties': {'frozenRowCount': 1, 'frozenColumnCount': 0}},
            'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
        {'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': n_rows,
                      'startColumnIndex': 0, 'endColumnIndex': WIDTH},
            'cell': {'userEnteredFormat': {'wrapStrategy': 'OVERFLOW_CELL',
                                           'verticalAlignment': 'MIDDLE'}},
            'fields': 'userEnteredFormat(wrapStrategy,verticalAlignment)'}},
    ]
    for i, px in enumerate(_WIDTHS):
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                      'startIndex': i, 'endIndex': i + 1},
            'properties': {'pixelSize': px}, 'fields': 'pixelSize'}})
    # The Period columns clip (Kyle 09-11): no overflow into the spacer,
    # no wrap -- the link text is the label, the cell is the width.
    for s in BAND_STARTS:
        c = s + BAND_COLS - 1
        requests.append({'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 4, 'endRowIndex': n_rows,
                      'startColumnIndex': c, 'endColumnIndex': c + 1},
            'cell': {'userEnteredFormat': {'wrapStrategy': 'CLIP'}},
            'fields': 'userEnteredFormat.wrapStrategy'}})
    merges, jumps = [], []
    for spec in sheet.formats:
        if 'merge' in spec:
            merges.append({'mergeCells': {'range': _grid(sheet_id, spec['merge']),
                                          'mergeType': 'MERGE_ALL'}})
            continue
        if 'jump' in spec:
            jumps.append(spec['jump'])
            continue
        fmt = spec['format']
        fields = ','.join(sorted(fmt.keys()))
        requests.append({'repeatCell': {
            'range': _grid(sheet_id, spec['range']),
            'cell': {'userEnteredFormat': fmt},
            'fields': f'userEnteredFormat({fields})'}})
    requests.extend(merges)
    # Jump index: one rich-text cell, one link per section.
    for jump in jumps:
        text_parts, runs, pos = [], [], 0
        items = list(jump['items'])
        for n, (label, key) in enumerate(items):
            target = sheet.jump_targets.get(key)
            if text_parts:
                text_parts.append(' · ')
                pos += 3
            runs.append({'startIndex': pos,
                         'format': {'link': {'uri': f'#gid={sheet_id}&range=A{target}'},
                                    'foregroundColor': {'red': 0.067, 'green': 0.333, 'blue': 0.8},
                                    'underline': False} if target else {}})
            text_parts.append(label)
            pos += len(label)
            # A run may not start at the end of the string, so the plain
            # separator run is only emitted between items.
            if n < len(items) - 1:
                runs.append({'startIndex': pos, 'format': {}})
        requests.append({'updateCells': {
            'range': _grid(sheet_id, jump['cell']),
            'rows': [{'values': [{'userEnteredValue': {'stringValue': ''.join(text_parts)},
                                  'textFormatRuns': runs}]}],
            'fields': 'userEnteredValue,textFormatRuns'}})
    # Collapsible groups (native row groups). Collapse through the group
    # itself (updateDimensionGroup, which needs the group's depth) rather
    # than by hiding rows: hidden rows alone leave the group's collapsed
    # flag unset, so the gutter control reads wrong (found 09-11).
    def rng_of(start_n, end_n):
        return {'sheetId': sheet_id, 'dimension': 'ROWS',
                'startIndex': start_n - 1, 'endIndex': end_n}
    for start_n, end_n, _ in sheet.groups:
        requests.append({'addDimensionGroup': {'range': rng_of(start_n, end_n)}})
    for start_n, end_n, collapsed in sheet.groups:
        if not collapsed:
            continue
        depth = 1 + sum(1 for s2, e2, _ in sheet.groups
                        if s2 <= start_n and end_n <= e2 and (s2, e2) != (start_n, end_n))
        requests.append({'updateDimensionGroup': {
            'dimensionGroup': {'range': rng_of(start_n, end_n), 'depth': depth, 'collapsed': True},
            'fields': 'collapsed'}})
    return requests


def _stale_state_requests(spreadsheet, worksheet):
    meta = _sheets_call(f'meta {worksheet.title}', lambda: spreadsheet.fetch_sheet_metadata(
        {'fields': 'sheets(properties(sheetId),rowGroups,merges)'}))
    sheet = next((s for s in meta.get('sheets', [])
                  if s.get('properties', {}).get('sheetId') == worksheet.id), {})
    requests = []
    groups = sorted(sheet.get('rowGroups', ()), key=lambda g: -(g.get('depth') or 1))
    for group in groups:
        requests.append({'deleteDimensionGroup': {
            'range': {**group.get('range', {}), 'sheetId': worksheet.id}}})
    if groups:
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': worksheet.id, 'dimension': 'ROWS'},
            'properties': {'hiddenByUser': False}, 'fields': 'hiddenByUser'}})
    requests.append({'unmergeCells': {'range': {'sheetId': worksheet.id}}})
    return requests


def write_tab(spreadsheet, title, sheet):
    rows = sheet.rows
    needed_rows = len(rows) + 20
    try:
        worksheet = spreadsheet.worksheet(title)
        if worksheet.row_count < needed_rows or worksheet.col_count < WIDTH:
            _sheets_call(f'resize {title}', lambda ws=worksheet: ws.resize(
                rows=max(ws.row_count, needed_rows), cols=max(ws.col_count, WIDTH)))
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(f'create {title}', lambda: spreadsheet.add_worksheet(
            title=title, rows=needed_rows, cols=WIDTH))
    pre = _stale_state_requests(spreadsheet, worksheet)
    _sheets_call(f'reset {title}', lambda: spreadsheet.batch_update({'requests': pre}))
    _sheets_call(f'clear {title}', worksheet.clear)
    safe_rows = [[sheet_safe(c) for c in row] for row in rows]
    _sheets_call(f'update {title}', lambda: worksheet.update(
        safe_rows, 'A1', value_input_option='USER_ENTERED'))
    requests = _style_requests(worksheet.id, sheet, len(rows))
    # The Sheets API caps a single batch comfortably above this, but keep
    # the groups (which are order-sensitive) in one request each.
    for i in range(0, len(requests), 400):
        chunk = requests[i:i + 400]
        _sheets_call(f'style {title} [{i}]', lambda c=chunk: spreadsheet.batch_update({'requests': c}))
    print(f"[records-book] wrote tab: {title} ({len(rows)} rows)")
    return worksheet


def placement_order(current_titles, new_titles, replace_title,
                    after=ADVANCED_STANDINGS_TAB):
    """The whole strip after placing the Records book. Pure.

    `current_titles` is the workbook's tab order today, `new_titles` the
    book's tabs in Lifetime · Season · Matchup order. They slot directly
    after `after` when the workbook has it -- the standing renders' rule,
    so a weekly render and a Records-book render agree on the strip --
    and otherwise into `replace_title`'s own position, the 09-09 rule. A
    book with neither is left as it is. The standing tab is never
    removed; it is hidden by the caller."""
    standing = [t for t in current_titles if t not in new_titles]
    if after in standing:
        at = standing.index(after) + 1
    elif replace_title in standing:
        at = standing.index(replace_title)
    else:
        return list(current_titles)
    return standing[:at] + list(new_titles) + standing[at:]


def place_tabs(spreadsheet, gids_in_order, replace_title):
    """Move the new tabs to their slot (see placement_order) and hide the
    standing tab. Every tab gets its index in one batch, in strip order:
    that is the form the Sheets API honours for moves in either direction
    (a lone target index is read in before-the-move positions and only
    behaves for moves toward the front), and it is what the standing
    renderers' sort passes already do."""
    meta = _sheets_call('meta', lambda: spreadsheet.fetch_sheet_metadata(
        {'fields': 'sheets(properties(sheetId,title,index,hidden))'}))
    sheets = sorted(meta.get('sheets', []), key=lambda s: s['properties']['index'])
    props = {s['properties']['title']: s['properties'] for s in sheets}
    old = props.get(replace_title)
    if old is None:
        print(f"[records-book] no '{replace_title}' tab to replace; tabs left where created")
        return
    title_by_gid = {p['sheetId']: t for t, p in props.items()}
    new_titles = [title_by_gid[g] for g in gids_in_order if g in title_by_gid]
    order = placement_order([s['properties']['title'] for s in sheets], new_titles, replace_title)
    requests = []
    for i, title in enumerate(order):
        requests.append({'updateSheetProperties': {
            'properties': {'sheetId': props[title]['sheetId'], 'index': i}, 'fields': 'index'}})
    if not old.get('hidden'):
        requests.append({'updateSheetProperties': {
            'properties': {'sheetId': old['sheetId'], 'hidden': True}, 'fields': 'hidden'}})
    _sheets_call('place tabs', lambda: spreadsheet.batch_update({'requests': requests}))
    where = order.index(new_titles[0]) if new_titles else '-'
    print(f"[records-book] placed {len(new_titles)} tabs at index {where}; "
          f"'{replace_title}' hidden")


def write_records_book(sheet_id, tabs, client=None, replace_tab='Records'):
    """tabs: [(title, Sheet)]. Returns {title: gid}. With replace_tab, the
    written tabs take that tab's place (Lifetime · Season · Matchup) and
    it is hidden."""
    client = client or _get_authorized_client()
    spreadsheet = _sheets_call('open', lambda: client.open_by_key(sheet_id))
    gids = {}
    for title, sheet in tabs:
        ws = write_tab(spreadsheet, title, sheet)
        gids[title] = ws.id
    if replace_tab:
        place_tabs(spreadsheet, [gids[t] for t in PLACEMENT_ORDER if t in gids], replace_tab)
    return gids


def tab_url(sheet_id, gid):
    return f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}'
