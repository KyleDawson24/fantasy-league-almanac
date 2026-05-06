"""
output/sheets_writer.py

Writes league records to a Google Sheet. Two tabs:
  - All-Time Records
  - Current Season Records

Each tab is cleared and rewritten on every call (idempotent). Polarity
filter (per output/records.py) is applied so we don't write meaningless
"least of negative" type non-records to the Sheet.

Auth: OAuth user-flow with desktop client (configured via the GCP project
provisioned in Phase 6.3.1; see .env.example for the required env vars).
First run opens a browser tab for consent; the resulting token is cached
to output/.sheets_oauth_token.json (gitignored) and refreshed transparently
on subsequent runs.

Opt-in: callers should check os.getenv('SHEETS_OUTPUT_ID') first and skip
this module entirely when it's unset. write_records() raises if invoked
without the OAuth client config in place.
"""

import os
from pathlib import Path

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import records as records_module
from formatters import STAT_DISPLAY


# Sheets scope is sufficient for opening a Sheet by ID and writing to its
# tabs. Drive scope (drive.file etc.) would be needed for create/list/search
# operations -- we don't do any of those.
_OAUTH_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Cached user token. Lives next to this script (gitignored). Refresh
# tokens stay valid until revoked; the first-run browser flow only fires
# once unless the file is deleted.
_TOKEN_PATH = Path(__file__).parent / '.sheets_oauth_token.json'

_HEADER = [
    'Scope', 'Grain', 'Stat', 'Direction',
    'Holder', 'Team Abbrev', 'Owner',
    'Value', 'Season', 'Week',
]

_TAB_TITLES = {
    'all_time':       'All-Time Records',
    'current_season': 'Current Season Records',
}


def write_records(sheet_id):
    """Top-level entry. Fetches all-time + current-season records via
    output/records.py, applies polarity filter, writes to the two tabs.

    Raises if GOOGLE_OAUTH_CLIENT_PATH isn't set or the OAuth flow fails.
    """
    client = _get_authorized_client()
    spreadsheet = client.open_by_key(sheet_id)
    polarity = records_module.get_stat_polarity()

    all_time = records_module.get_all_time_records()
    current  = records_module.get_current_season_records()

    all_time_rows = _build_tab_rows('All-Time',       all_time, polarity)
    current_rows  = _build_tab_rows('Current Season', current,  polarity)

    _replace_tab(spreadsheet, _TAB_TITLES['all_time'],       all_time_rows)
    _replace_tab(spreadsheet, _TAB_TITLES['current_season'], current_rows)

    print(
        f"[sheets] wrote {len(all_time_rows)} all-time + "
        f"{len(current_rows)} current-season records to sheet {sheet_id}"
    )


def _build_tab_rows(scope_label, leaderboard_rows, polarity):
    """Filter to records the polarity rules say to surface, project to the
    tab schema, and translate to display-friendly labels.

    Output: list of lists, each inner list aligned to _HEADER columns.
    Sorted by (grain rank, stat rank, direction rank) so the Sheet has a
    consistent display order across runs.
    """
    out = []
    for r in leaderboard_rows:
        grain = r['entity_grain']
        stat = r['stat_name']
        direction = r['record_direction']
        if not records_module.should_track_record(grain, stat, direction, polarity):
            continue

        # Display labels: friendly stat name, friendly direction word.
        stat_label = STAT_DISPLAY.get(stat, stat)
        direction_label = _direction_label(stat, direction)
        holder = r['team_name'] if grain == 'team' else r['display_name']

        out.append([
            scope_label,
            grain.title(),       # 'team' -> 'Team'
            stat_label,
            direction_label,
            holder,
            r.get('team_abbrev') or '',
            r.get('owner_name')  or '',
            r['stat_value'],
            r['season_year'],
            r['matchup_period'],
        ])

    out.sort(key=_row_sort_key)
    return out


def _direction_label(stat, direction):
    """'Best' / 'Worst' for score columns, 'Most' / 'Fewest' for counting
    stats. Matches the bolded labels generate_summary.py uses for new-
    record callouts so the Sheet reads consistently with the BBCode."""
    # Phase 6.3.3: mart direction values are 'most' / 'fewest' now.
    if stat in records_module.SCORE_STAT_NAMES:
        return 'Best' if direction == 'most' else 'Worst'
    return 'Most' if direction == 'most' else 'Fewest'


def _row_sort_key(row):
    """Order: Player records first, then Team score, then Team stat;
    within each, Best/Most before Worst/Fewest. Matches the visual order
    used by the BBCode summary's records sections."""
    grain_label = row[1]   # 'Team' / 'Player'
    stat_label  = row[2]
    direction   = row[3]   # 'Best' / 'Worst' / 'Most' / 'Fewest'

    grain_rank = 0 if grain_label == 'Player' else 1
    score_label_set = {STAT_DISPLAY[s] for s in records_module.SCORE_STAT_NAMES}
    score_order = {
        STAT_DISPLAY['CALCULATED_POINTS']:        0,
        STAT_DISPLAY['CALCULATED_HITTING_PTS']:   1,
        STAT_DISPLAY['CALCULATED_PITCHING_PTS']:  2,
    }

    if stat_label in score_label_set:
        category_rank = 0 if grain_rank == 1 else 0
        stat_rank = score_order.get(stat_label, 99)
    else:
        category_rank = 1
        stat_rank = 99   # individual stats lumped after score columns

    direction_rank = 0 if direction in ('Best', 'Most') else 1
    return (grain_rank, category_rank, stat_rank, stat_label, direction_rank)


def _replace_tab(spreadsheet, title, rows):
    """Idempotent write: clear the tab (creating it if missing) and
    rewrite header + rows."""
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=title, rows=max(len(rows) + 10, 50), cols=len(_HEADER),
        )
    worksheet.clear()
    worksheet.update([_HEADER] + rows, 'A1')


def _get_authorized_client():
    """Returns an authorized gspread client. First run opens a browser for
    consent; subsequent runs use cached credentials and refresh
    transparently."""
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(
            str(_TOKEN_PATH), _OAUTH_SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_path = os.getenv('GOOGLE_OAUTH_CLIENT_PATH')
            if not client_path:
                raise RuntimeError(
                    "GOOGLE_OAUTH_CLIENT_PATH env var not set. "
                    "Configure GCP OAuth client per the Phase 6.3.1 setup steps."
                )
            if not Path(client_path).exists():
                raise RuntimeError(
                    f"OAuth client config not found at {client_path}. "
                    f"Check GOOGLE_OAUTH_CLIENT_PATH in .env."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                client_path, _OAUTH_SCOPES,
            )
            # port=0 picks an arbitrary free port for the redirect handler.
            creds = flow.run_local_server(port=0)
        with open(_TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())

    return gspread.authorize(creds)
