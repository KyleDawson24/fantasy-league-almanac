"""
output/sheets_writer.py

Writes league records to a Google Sheet. Three tabs:
  - All-Time Records       (rank-1 record holder per stat x direction, with contributors)
  - Current Season Records (same shape, scoped to active season)
  - Leaderboard Dump       (top-5 per stat x direction x scope, both scopes interleaved,
                            tie-collapse + contributors via records.get_records_with_contributors)

Each tab is cleared and rewritten on every call (idempotent). All three
tabs run through records.get_records_with_contributors(), which applies
the chunk-3 layered filter (polarity + always-tracked seed flag + team-
grain rate stats / wasted_points / derived stats) and the chunk-4 tie-
collapse. The 17-col schema includes Rank + 3 contributor pairs:
  - Team-grain rows: Contributor1..3 = player names, Count1..3 = stat values
  - Player-grain rows: Contributor1..3 = stat names (HR/K/etc),
                       Count1..3 = counts (NOT pts; per locked decision)
  - Collapsed rows ('N teams tied at X'): Holder = 'N teams', contributor cols blank

Auth: OAuth user-flow with desktop client (configured via the GCP project
provisioned in Phase 6.3.1; see .env.example for the required env vars).
First run opens a browser tab for consent; the resulting token is cached
to output/.sheets_oauth_token.json (gitignored) and refreshed transparently
on subsequent runs.

Opt-in: callers resolve the target via sheets_target.resolve_sheets_target()
(the dev sheet by default, production with --prod) and skip this module
entirely when no target is configured. write_records() raises if invoked
without the OAuth client config in place.
"""

import functools
import os
from pathlib import Path

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import records as records_module
import stat_catalog
from formatters import fmt_ip


# Sheets scope is sufficient for opening a Sheet by ID and writing to its
# tabs. Drive scope (drive.file etc.) would be needed for create/list/search
# operations -- we don't do any of those.
_OAUTH_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Cached user token. Lives next to this script (gitignored). Refresh
# tokens stay valid until revoked; the first-run browser flow only fires
# once unless the file is deleted.
_TOKEN_PATH = Path(__file__).parent / '.sheets_oauth_token.json'

_HEADER = [
    'Scope', 'Grain', 'Stat', 'Direction', 'Rank',
    'Holder', 'Team Abbrev', 'Owner',
    'Value', 'Season', 'Week',
    'Contributor1', 'Count1',
    'Contributor2', 'Count2',
    'Contributor3', 'Count3',
]

_TAB_TITLES = {
    'all_time':         'All-Time Records',
    'current_season':   'Current Season Records',
    'leaderboard_dump': 'Leaderboard Dump',
}

# Score-stat label set used by _row_sort_key to partition score rows
# ahead of stat rows. Phase 7 G1: built lazily from the seed-driven
# stat_catalog (cached at first access).
@functools.lru_cache(maxsize=1)
def _score_label_set():
    display = stat_catalog.get_display_map()
    return frozenset({
        display['CALCULATED_POINTS'],
        display['CALCULATED_HITTING_PTS'],
        display['CALCULATED_PITCHING_PTS'],
    })


def write_records(sheet_id):
    """Top-level entry. Pulls records via the orchestrator
    (records.get_records_with_contributors -- polarity + always-tracked
    + non-seed-stat filter + tie-collapse + bulk contributors stitched
    in) and writes the three tabs.

    Raises if GOOGLE_OAUTH_CLIENT_PATH isn't set or the OAuth flow fails.
    """
    client = _get_authorized_client()
    spreadsheet = client.open_by_key(sheet_id)
    schedule_lookup = records_module.load_schedule_lookup()
    # Phase 6.3.3 chunk 6.5: polarity-aware Best/Worst labels everywhere.
    # Phase 7 G2: polarity now comes from stat_catalog (seed-driven; the
    # B1 regen baked the scoring-settings + implicit overrides merge into
    # stat_classification.polarity).
    effective_polarity = stat_catalog.get_polarity_map()

    # Tabs 1 & 2: rank-1 holder per (stat, direction). top_n=1 in the
    # orchestrator means the tie-collapse algorithm doesn't have an
    # overflow tier to detect (max_n=5 default; 1 visible row trivially
    # under cap), so all rank-1 rows pass through individually.
    all_time_rank1 = records_module.get_records_with_contributors('all_time',         top_n=1)
    current_rank1  = records_module.get_records_with_contributors('current_season',   top_n=1)

    # Tab 3 (Leaderboard Dump): top-5 per partition. Tie-collapse fires
    # for groups whose tier extends past rank 5 (e.g. CG/NH/PG zero-floor
    # ties with N in the hundreds collapse to one synthetic row).
    all_time_top5  = records_module.get_records_with_contributors('all_time',         top_n=5)
    current_top5   = records_module.get_records_with_contributors('current_season',   top_n=5)

    all_time_rows = _build_rows('All-Time',       all_time_rank1, schedule_lookup, effective_polarity)
    current_rows  = _build_rows('Current Season', current_rank1,  schedule_lookup, effective_polarity)

    # Tab 3 interleaves both scopes. Build each separately (so the sort
    # key sees a consistent scope label per record) then concatenate;
    # the Scope column distinguishes the two slabs visually.
    dump_rows = (
        _build_rows('All-Time',       all_time_top5, schedule_lookup, effective_polarity) +
        _build_rows('Current Season', current_top5,  schedule_lookup, effective_polarity)
    )

    _replace_tab(spreadsheet, _TAB_TITLES['all_time'],         all_time_rows)
    _replace_tab(spreadsheet, _TAB_TITLES['current_season'],   current_rows)
    _replace_tab(spreadsheet, _TAB_TITLES['leaderboard_dump'], dump_rows)

    print(
        f"[sheets] wrote {len(all_time_rows)} all-time + "
        f"{len(current_rows)} current-season + "
        f"{len(dump_rows)} leaderboard-dump rows to sheet {sheet_id}"
    )


def _build_rows(scope_label, records, schedule_lookup, effective_polarity):
    """Project orchestrator records into 17-col tab rows, sort, and
    return. Records already passed the chunk-3 layered filter and chunk-4
    tie-collapse upstream; this layer is purely formatting + ordering.
    """
    rows = [
        _format_row(r, scope_label, schedule_lookup, effective_polarity)
        for r in records
    ]
    rows.sort(key=_row_sort_key)
    return rows


def _format_row(record, scope_label, schedule_lookup, effective_polarity):
    """Build a 17-column list for one orchestrator record. Three shapes:
      - Real team record:    Holder = team_name; contributors = top-3 player names + counts
      - Real player record:  Holder = display_name; contributors = top-3 stat names + counts
      - Collapsed synthetic: Holder = 'N teams' / 'N players'; contributor cols blank

    Rate stats / WASTED_POINTS records have empty contributors (no per-
    player breakdown story; orchestrator already returns []) -- nothing
    special needed here, the contributor loop just produces empty cells.
    """
    grain = record['entity_grain']
    stat_name = record['stat_name']
    direction = record['record_direction']
    stat_label = stat_catalog.get_display_map().get(stat_name, stat_name)
    direction_label = records_module.best_or_worst_label(
        stat_name, direction, effective_polarity,
    )

    season = record.get('season_year')
    mp     = record.get('matchup_period')
    week_label = (records_module.format_week_label(season, mp, schedule_lookup)
                  if (season is not None and mp is not None)
                  else '')
    season_cell = season if season is not None else ''

    # Phase 6.3.3 chunk 6.5: OUTS Value column converts raw outs to
    # baseball IP notation (51 outs -> "17.0"). Other stats keep raw
    # numeric values for clean Sheets numeric sorting.
    raw_value = record.get('stat_value')
    value_cell = fmt_ip(raw_value) if stat_name == 'OUTS' else raw_value

    if record.get('is_collapsed'):
        # Phase 6.3.3 chunk 6.6: small overflow tiers (<=
        # INLINE_COLLAPSE_THRESHOLD) carry the underlying tied rows on
        # the synthetic via 'holders'; render their identities inline
        # as comma-joined cells. Above the threshold (or when the mart
        # buffer saturated and we don't have all members in memory),
        # holders=[] and we fall back to the legacy "N teams" summary.
        holders = record.get('holders') or []
        if holders:
            if grain == 'team':
                holder = ", ".join(h.get('team_abbrev') or '' for h in holders)
            else:
                holder = ", ".join(h.get('display_name') or '' for h in holders)
            # Each holder's matchup_period gets its own week label so
            # playoff rounds vs regular weeks render correctly per row.
            week_label  = ", ".join(
                records_module.format_week_label(
                    h.get('season_year'), h.get('matchup_period'), schedule_lookup,
                )
                for h in holders
            )
            season_cell = ", ".join(str(h.get('season_year')) for h in holders)
        else:
            unit = 'teams' if grain == 'team' else 'players'
            holder = f"{record.get('tie_count', 0)} {unit}"
        team_abbrev  = ''
        owner        = ''
        contrib_cols = ['', '', '', '', '', '']
        rank_cell    = 'collapsed'
    else:
        if grain == 'team':
            holder = record.get('team_name') or ''
        else:
            holder = record.get('display_name') or ''
        team_abbrev = record.get('team_abbrev') or ''
        owner       = record.get('owner_name') or ''
        contrib_cols = _contributor_cells(
            grain, stat_name, record.get('contributors') or [],
        )
        rank_cell   = record.get('rank')

    return [
        scope_label,
        grain.title(),
        stat_label,
        direction_label,
        rank_cell,
        holder,
        team_abbrev,
        owner,
        value_cell,
        season_cell,
        week_label,
        *contrib_cols,
    ]


def _contributor_cells(grain, leaderboard_stat, contributors):
    """Flatten up to 3 contributors into [Contributor1, Count1, ...
    Contributor3, Count3]. Pads with empty strings.

    Team-grain contributor dict shape: {display_name, stat_value}
      -- count is the player's contribution to the LEADERBOARD stat
         (e.g., player's outs for a team's "Most OUTS" record)

    Player-grain contributor dict shape: {stat_name, count_value, point_value}
      -- count is the player's count of THAT contributor's own stat
         (e.g., 51 outs for an Innings Pitched contributor)

    Phase 6.3.3 chunk 6.5: OUTS counts convert to baseball IP notation
    in both grains. For team grain we look at leaderboard_stat (the
    team's leaderboard stat); for player grain we look at each
    contributor's stat_name independently.
    """
    cells = []
    for i in range(3):
        if i < len(contributors):
            c = contributors[i]
            if grain == 'team':
                cells.append(c.get('display_name') or '')
                raw = c.get('stat_value')
                cells.append(
                    fmt_ip(raw) if leaderboard_stat == 'OUTS' else raw
                )
            else:
                # Player-grain: surface stat NAME (uppercase column key)
                # and count -- not pts. Per locked decision #4.
                stat_key = c.get('stat_name', '')
                cells.append(stat_catalog.get_display_map().get(stat_key, stat_key))
                raw = c.get('count_value')
                cells.append(fmt_ip(raw) if stat_key == 'OUTS' else raw)
        else:
            cells.append('')
            cells.append('')
    return cells


def _row_sort_key(row):
    """Phase 6.3.3 chunk 6.6: 4-cohort visual order across all three tabs.

    Top-level cohorts (in this order):
      1. Best Scores  (direction='Best',  score-stat)
      2. Best Stats   (direction='Best',  non-score)
      3. Worst Scores (direction='Worst', score-stat)
      4. Worst Stats  (direction='Worst', non-score)

    Within each cohort:
      - Stat label alphabetical (CALCULATED_*  use STAT_DISPLAY labels:
        'Hitting Points' / 'Pitching Points' / 'Total Points'; alphabetic
        ordering within score cohort yields H -> P -> T which reads
        cleanly)
      - Scope: All-Time before Current Season (only Tab 3 has both)
      - Grain: Player before Team
      - Rank ascending, with synthetic 'collapsed' rows sorting last
    """
    scope_label  = row[0]
    grain_label  = row[1]
    stat_label   = row[2]
    direction    = row[3]
    rank_cell    = row[4]

    direction_rank = 0 if direction == 'Best' else 1
    score_rank     = 0 if stat_label in _score_label_set() else 1
    scope_rank     = 0 if scope_label == 'All-Time' else 1
    grain_rank     = 0 if grain_label == 'Player' else 1
    rank_int       = rank_cell if isinstance(rank_cell, int) else 999

    return (direction_rank, score_rank, stat_label,
            scope_rank, grain_rank, rank_int)


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
