"""Sheets writer for the redesigned Records tabs (MLB-212 dev render).

One writer for both books. It knows the eighteen-column geometry the
presenter emits and nothing about either league: it creates or replaces
the three tabs, writes values USER_ENTERED (so the =HYPERLINK cells
parse), paints the presenter's format spec, adds the collapsible row
groups, freezes the title band, and resolves the jump index into in-sheet
links once the tab's gid exists.

Tab placement (spec section 1, 09-09): on the DEV book the three tabs
take the standing Records tab's position, ordered Lifetime · Season ·
Matchup, and the standing tab is hidden -- never deleted, so the dev
book keeps the old page for comparison. Prod never comes through here.
"""

import gspread

from almanac_write import _is_quota_error, _sheets_call
from records_book_logic import TITLES, WIDTH, _col

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
    # Collapsible groups (native row groups); collapsed ones hide their rows.
    for start_n, end_n, collapsed in sheet.groups:
        rng = {'sheetId': sheet_id, 'dimension': 'ROWS',
               'startIndex': start_n - 1, 'endIndex': end_n}
        requests.append({'addDimensionGroup': {'range': rng}})
        if collapsed:
            requests.append({'updateDimensionProperties': {
                'range': rng, 'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}})
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
    _sheets_call(f'update {title}', lambda: worksheet.update(
        rows, 'A1', value_input_option='USER_ENTERED'))
    requests = _style_requests(worksheet.id, sheet, len(rows))
    # The Sheets API caps a single batch comfortably above this, but keep
    # the groups (which are order-sensitive) in one request each.
    for i in range(0, len(requests), 400):
        chunk = requests[i:i + 400]
        _sheets_call(f'style {title} [{i}]', lambda c=chunk: spreadsheet.batch_update({'requests': c}))
    print(f"[records-book] wrote tab: {title} ({len(rows)} rows)")
    return worksheet


def place_tabs(spreadsheet, gids_in_order, replace_title):
    """Move the new tabs into the standing tab's slot, in order, and hide
    the standing tab. Idempotent: the slot is the lowest index among the
    standing tab and the new tabs, so a re-render never shuffles them
    (the API reads a target index in before-the-move positions, which
    only behaves for moves toward the front)."""
    meta = _sheets_call('meta', lambda: spreadsheet.fetch_sheet_metadata(
        {'fields': 'sheets(properties(sheetId,title,index,hidden))'}))
    props = {s['properties']['title']: s['properties'] for s in meta.get('sheets', [])}
    old = props.get(replace_title)
    if old is None:
        print(f"[records-book] no '{replace_title}' tab to replace; tabs left where created")
        return
    indexes = [old['index']] + [props[t]['index'] for t in props if props[t]['sheetId'] in gids_in_order]
    base = min(indexes)
    requests = []
    for i, gid in enumerate(gids_in_order):
        requests.append({'updateSheetProperties': {
            'properties': {'sheetId': gid, 'index': base + i}, 'fields': 'index'}})
    if not old.get('hidden'):
        requests.append({'updateSheetProperties': {
            'properties': {'sheetId': old['sheetId'], 'hidden': True}, 'fields': 'hidden'}})
    _sheets_call('place tabs', lambda: spreadsheet.batch_update({'requests': requests}))
    print(f"[records-book] placed {len(gids_in_order)} tabs at index {base}; "
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
