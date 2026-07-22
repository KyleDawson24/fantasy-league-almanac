#!/usr/bin/env python3
"""Assemble the commissioner's one-doc League History sheet (FIL kickoff).

Per Kyle 2026-07-20: the collection surface is THREE pieces in ONE existing
Google Sheet -- the League Provenance Override (fills preserved; this script
NEVER touches existing tabs) plus two tabs this script adds:

  1. "2001-02 Rosters" -- copied verbatim (values, formats, validation) from
     the CBS dev almanac's backfill tab, by sheet gid. The league's first two
     seasons are the biggest hole in the history; the dev tab is already
     dropdown-driven for painless entry.
  2. "Old Draft Records" -- a submit-first form: the primary ask is "forward
     me whatever files/emails you have" (we parse; the human never types 300
     picks), with a small optional manual grid for anyone who prefers typing.

Add-only and rerun-safe: a tab that already exists in the destination is
skipped, never overwritten -- so the commissioner's in-progress answers
survive any rerun. (Contrast build_continuity_sheet.py, which re-renders its
four tabs in place: do NOT re-run that against this sheet once he starts
filling; harvest first.)

Usage (from the repo root):
    python tools/add_fil_tabs.py --dest <provenance sheet id-or-url>
    # dev sheet defaults to CBS_SHEETS_DEV_ID; backfill gid defaults to the
    # known tab. Override with --dev / --backfill-gid if either ever moves.

Auth: same cached user token as every sheets writer
(output/.sheets_oauth_token.json); no new consent flow.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import gspread
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

_OAUTH_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
_TOKEN_PATH = Path(__file__).resolve().parent.parent / 'output' / '.sheets_oauth_token.json'

BACKFILL_GID_DEFAULT = 1366638907
BACKFILL_TITLE = '2001-02 Rosters'
DRAFT_TITLE = 'Old Draft Records'

YELLOW = {'red': 1.0, 'green': 0.949, 'blue': 0.8}
NAVY_TEXT = {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}, 'bold': True}
NAVY_BG = {'red': 0.122, 'green': 0.220, 'blue': 0.392}
GRAY_BG = {'red': 0.949, 'green': 0.949, 'blue': 0.949}


def _load_env():
    if load_dotenv is None:
        return
    # .env lives at the repo root; resolve upward like the other writers.
    here = Path(__file__).resolve().parent
    for cand in (here.parent, *here.parent.parents):
        envf = cand / '.env'
        if envf.exists():
            load_dotenv(envf)
            return


def _run_consent_flow():
    client_path = os.getenv('GOOGLE_OAUTH_CLIENT_PATH')
    if not client_path or not Path(client_path).exists():
        raise RuntimeError(
            'GOOGLE_OAUTH_CLIENT_PATH unset or missing; cannot open the '
            'OAuth consent flow. See the Phase 6.3.1 setup steps.'
        )
    flow = InstalledAppFlow.from_client_secrets_file(client_path, _OAUTH_SCOPES)
    return flow.run_local_server(port=0)


def _client():
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _OAUTH_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = _run_consent_flow()
        else:
            creds = _run_consent_flow()
        with open(_TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
    return gspread.authorize(creds)


def _sheet_id(id_or_url):
    m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', id_or_url or '')
    return m.group(1) if m else id_or_url


def _ws_by_gid(spreadsheet, gid):
    for ws in spreadsheet.worksheets():
        if ws.id == int(gid):
            return ws
    return None


def _copy_backfill(gc, dest, dev_id, gid):
    if any(ws.title == BACKFILL_TITLE for ws in dest.worksheets()):
        print(f'  ~ "{BACKFILL_TITLE}" already exists -- skipped (never overwritten).')
        return
    dev = gc.open_by_key(dev_id)
    src = _ws_by_gid(dev, gid)
    if src is None:
        sys.exit(f'ERROR: no tab with gid {gid} on the dev sheet ({dev.title}).')
    print(f'  copying dev tab "{src.title}" (gid {gid}) -> "{dest.title}" ...')
    resp = src.copy_to(dest.id)
    new_ws = _ws_by_gid(dest, resp['sheetId'])
    new_ws.update_title(BACKFILL_TITLE)
    print(f'  + "{BACKFILL_TITLE}" added (was "{resp.get("title", "?")}").')
    print('    NOTE: open it and test one dropdown -- if a list sourced from a')
    print('    hidden helper tab on dev, the copy loses it and we adapt.')


DRAFT_QUESTIONS = [
    'Do you have old CBS draft-results emails or files anywhere? (Yes / No / Not sure)',
    'Roughly which years might you have anything for?',
    'In 2001-2008, did the league draft ON the CBS site, or offline (in person / '
    'email) with rosters entered afterward?',
    'CBS shows the 2020 draft recorded twice -- was that one draft or two (a re-do)?',
    'Is there anyone else in the league who might keep old records?',
]

COVERAGE = [
    ('Years', 'What CBS still has', 'What we need'),
    ('2001-2008', 'Nothing survives', 'Anything at all -- even partial lists'),
    ('2009', 'Pick order only (player names lost)', 'Who was taken with those picks'),
    ('2010, 2012', 'Nothing survives', 'Anything at all'),
    ('2011-2023', 'Players by team, but no pick order', 'Draft order, if anyone has it'),
    ('2024', 'Pick-order skeleton, player names missing', 'Who was taken with each pick'),
    ('2025-2026', 'Complete', 'Nothing -- all set'),
]


def _add_draft_form(dest):
    if any(ws.title == DRAFT_TITLE for ws in dest.worksheets()):
        print(f'  ~ "{DRAFT_TITLE}" already exists -- skipped (never overwritten).')
        return
    ws = dest.add_worksheet(title=DRAFT_TITLE, rows=90, cols=10)
    print(f'  + "{DRAFT_TITLE}" added; writing content ...')

    instruction = (
        "You don't need to type anything here -- if you have ANY old draft "
        "records (emails, files, printouts, photos), just forward them to "
        "kpdawson24@gmail.com and I'll do the rest. The questions below tell "
        "me what to go looking for. And if typing a few picks IS easier for "
        "you, the optional grid at the bottom works too."
    )
    rows = [[instruction] + [''] * 9]
    rows.append([''] * 10)
    for q in DRAFT_QUESTIONS:
        rows.append([q, '', '', '', '', '', '', '', '', ''])
    rows.append([''] * 10)
    # coverage reference
    for r in COVERAGE:
        rows.append([r[0], r[1], r[2]] + [''] * 7)
    rows.append([''] * 10)
    rows.append(['Optional: type picks here if you prefer'] + [''] * 9)
    rows.append(['Year', 'Round', 'Pick in round', 'Team (who drafted)', 'Player',
                 'Notes', '', '', '', ''])
    ws.update(values=rows, range_name=f'A1:J{len(rows)}')

    n_q0 = 3                       # first question row
    n_q1 = n_q0 + len(DRAFT_QUESTIONS) - 1
    cov0 = n_q1 + 2                # coverage header row
    grid_note = cov0 + len(COVERAGE) + 1
    grid_hdr = grid_note + 1

    fmt = [
        ('A1:J1', {'textFormat': {'italic': True}, 'backgroundColor': GRAY_BG,
                   'wrapStrategy': 'WRAP'}),
        (f'A{n_q0}:A{n_q1}', {'wrapStrategy': 'WRAP'}),
        (f'B{n_q0}:D{n_q1}', {'backgroundColor': YELLOW}),
        (f'A{cov0}:C{cov0}', {'textFormat': NAVY_TEXT, 'backgroundColor': NAVY_BG}),
        (f'A{cov0 + 1}:C{cov0 + len(COVERAGE) - 1}', {'wrapStrategy': 'WRAP'}),
        (f'A{grid_note}', {'textFormat': {'bold': True}}),
        (f'A{grid_hdr}:F{grid_hdr}', {'textFormat': NAVY_TEXT, 'backgroundColor': NAVY_BG}),
        (f'A{grid_hdr + 1}:F{grid_hdr + 60}', {'backgroundColor': YELLOW}),
    ]
    for rng, f in fmt:
        ws.format(rng, f)
        time.sleep(1.1)            # polite pacing, same lesson as the writers
    ws.freeze(rows=1)
    ws.rows_auto_resize(0, 1)
    print(f'    questions rows {n_q0}-{n_q1}, coverage at {cov0}, grid from {grid_hdr}.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dest', required=True,
                    help='the League Provenance Override sheet (id or url)')
    ap.add_argument('--dev', default=None,
                    help='the CBS dev almanac (id or url); default CBS_SHEETS_DEV_ID')
    ap.add_argument('--backfill-gid', type=int, default=BACKFILL_GID_DEFAULT)
    args = ap.parse_args()

    _load_env()
    dev_id = _sheet_id(args.dev) if args.dev else os.getenv('CBS_SHEETS_DEV_ID')
    if not dev_id:
        sys.exit('ERROR: no dev sheet -- pass --dev or set CBS_SHEETS_DEV_ID in .env')

    gc = _client()
    dest = gc.open_by_key(_sheet_id(args.dest))
    print(f'Destination: "{dest.title}"')
    print(f'  existing tabs (untouched): {[w.title for w in dest.worksheets()]}')
    _copy_backfill(gc, dest, dev_id, args.backfill_gid)
    _add_draft_form(dest)
    print('Done. Suggested finish: rename the sheet to something commissioner-'
          'friendly (e.g. "BSB League History"), drag the two new tabs to taste, '
          'share as EDITOR.')


if __name__ == '__main__':
    main()
