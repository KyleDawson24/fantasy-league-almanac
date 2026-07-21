"""
Build the League Continuity Mapping sheet -- a hand-to-a-human override
surface for franchise + owner identity (MLB-64).

WHAT IT IS
    A Google Sheet a league historian (Kyle's father-in-law, a commissioner,
    anyone non-technical) fills in to assert the truths the raw platform data
    can't: which team IDs are narratively the SAME club across renames and
    id-reminting, which owner handles are the same human, and who owned what
    in which year. Everything the platform DOES tell us is pre-filled and
    locked; the human only fills blanks (or ignores them -- a blank is always
    a valid answer meaning "the observed value is correct").

    Three data tabs + a READ ME, each led by Platform / League / native id so
    the identical structure ingests any future platform (this is deliberately
    NOT cbs-specific in shape):

      Teams   (one row per team id)      -- franchise continuity + canon name
      Owners  (one row per owner handle) -- person identity + preferred name
      Team+Owner by Year (the bridge)    -- per-season custody

THE "SAME AS (CANONICAL)" MECHANISM  (teams + owners)
    One column does merges AND cross-league stitching. Resolution downstream
    is COALESCE(same_as, league_key || '::' || native_id):
      - blank          -> auto-namespaced to this league (every league has a
                          "1"; the namespace keeps them from colliding)
      - an earlier id  -> merge within this league; the earliest id is the
                          canonical anchor (Kyle's call, 2026-07-14)
      - a shared label -> stitch across leagues/platforms. Put the same made-up
                          label on an ESPN row and a CBS row and they become
                          one franchise with one continuous history. This is
                          the league-migration superpower; the shape already
                          supports it with no later change.

PRE-FILL POLICY
    Teams.Same As is pre-filled ONLY for unambiguous remints (a later id that
    shares a name+abbrev with exactly one earlier id, <=1 season gap -- the
    "sat out a short season and came back" pattern: 30->13, 28->22). Anything
    with an overlap (coexisted -> distinct) or a fork (one old id claimed by
    two new ones -> the VCF/Deuces tangle) is left blank with a hint, for the
    human + the commissioner conversation (MLB-51).

    Bridge.Owner-assumed is lever (1) of the tab-3 autofill: today's owner
    propagated across each ACTIVE franchise's whole span. Lever (2) -- back-
    propagating an owner over a retired id once Teams links it -- and lever
    (3) -- the year-end roster-title parse (2008+) reading real owners per
    season -- are follow-ons that shrink the manual residue to pre-2008.

AUTH / OWNERSHIP
    Reuses the almanac writers' cached user OAuth token (output/
    .sheets_oauth_token.json, spreadsheets scope). That token is the USER's,
    so the sheet must already exist and be owned by them -- we can't mint one
    (no Drive scope, by design). Pass its id/url with --sheet-id; the sheet
    stays in the user's Drive, theirs to share.

USAGE
    python build_continuity_sheet.py --league cbs-bsb --preview-dir ./out
    python build_continuity_sheet.py --league cbs-bsb --sheet-id <id-or-url>

    Idempotent: a rerun overwrites the four tabs in place (values, formats,
    and protected ranges all reset first), so regenerating after new data is
    safe. --preview-dir writes TSVs and never touches Sheets (the same test-
    safety convention as the almanac).
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

import db


# --------------------------------------------------------------------------
# Auth -- mirrors sheets_writer._get_authorized_client and shares its cached
# token file so no second browser consent is ever triggered.
# --------------------------------------------------------------------------
_OAUTH_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
_TOKEN_PATH = Path(__file__).parent / '.sheets_oauth_token.json'


def _run_consent_flow():
    client_path = os.getenv('GOOGLE_OAUTH_CLIENT_PATH')
    if not client_path or not Path(client_path).exists():
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_PATH unset or missing; cannot open the "
            "OAuth consent flow. See the Phase 6.3.1 setup steps."
        )
    flow = InstalledAppFlow.from_client_secrets_file(client_path, _OAUTH_SCOPES)
    return flow.run_local_server(port=0)


def _get_authorized_client():
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH),
                                                      _OAUTH_SCOPES)
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


# --------------------------------------------------------------------------
# Observed-data extraction.
#
# Structured as one adapter per platform so a new platform is a new function,
# not a rewrite. Today only CBS is wired (its history lives in the parsed UI
# standings + the curated owner seeds); an ESPN adapter would read its own
# sources and return the same three shapes.
# --------------------------------------------------------------------------

def _q(sql):
    return db.query_snowflake(sql)


# Sheets throws transient 500/503 ("service unavailable") and 429 (per-minute
# write quota); the almanac writers learned to back off past the minute window
# rather than crash. Same lesson here -- one un-retried 503 on a clear() was
# enough to leave the sheet half-written.
_RETRY_WAITS = [5, 15, 40, 70]


def _retry(label, fn):
    for attempt in range(len(_RETRY_WAITS) + 1):
        try:
            return fn()
        except gspread.exceptions.APIError as exc:
            msg = str(exc).lower()
            transient = any(s in msg for s in
                            ('[500]', '[503]', '[429]', 'unavailable',
                             'rate limit', 'quota exceeded', 'internal error'))
            if attempt == len(_RETRY_WAITS) or not transient:
                raise
            wait = _RETRY_WAITS[attempt]
            print(f"  [retry] {label}: {msg[:70]}; waiting {wait}s")
            time.sleep(wait)


def _cbs_team_seasons(league_key):
    """(year, team_id, team_name) for every observed team-season.

    Completed seasons come from the UI standings parse (2001..last finished
    year). The LIVE season is absent there -- it's served by the daily roster
    capture -- so its teams are unioned in (latest captured name per team).
    Without this a rename made THIS year is invisible (id 1 -> 'Firefly Lake
    Veronicas' in 2026 would still read as 2025's 'Mesa Javelinas'). Bare table
    names resolve against db's default ANALYTICS schema."""
    completed = _q(f"""
        select season_year, franchise_id, team_name
        from stg_cbs__ui_standings
        where league_key = '{league_key}'""")
    live = _q(f"""
        select season_year, team_id as franchise_id, team_name
        from stg_cbs__rosters
        where league_key = '{league_key}' and team_name is not null
        qualify row_number() over (
            partition by season_year, team_id order by roster_date desc) = 1""")
    done_years = {int(r['season_year']) for r in completed}
    rows = [(int(r['season_year']), str(r['franchise_id']), r['team_name'])
            for r in completed]
    rows += [(int(r['season_year']), str(r['franchise_id']), r['team_name'])
             for r in live if int(r['season_year']) not in done_years]
    rows.sort(key=lambda t: (int(t[1]), t[0]))
    return rows


def _name_story(seasons_for_id):
    """['Name (2001-2002)', 'Name (2003)'] collapsing consecutive repeats."""
    story = []
    for y, _, nm in seasons_for_id:
        if story and story[-1][0] == nm:
            story[-1][2] = y
        else:
            story.append([nm, y, y])
    return [f"{nm} ({a}-{b})" if b != a else f"{nm} ({a})"
            for nm, a, b in story]


def _norm_owner(o):
    """Tidy a parsed owner name: collapse whitespace, and title-case only the
    all-caps / all-lower ones ('JACK KLINE', 'matt pulse') so mixed-case names
    that are already right ('McNeal', \"Delgado\", 'L.J.') are left alone."""
    o = ' '.join((o or '').split())
    return o.title() if (o.isupper() or o.islower()) else o


def _split_owners(s):
    """Co-owned teams store 'A and B' / 'A & B' / 'A, B' in one owner_name
    cell -- and the tag-stripped source sometimes glues it ('Barry Sextonand
    Sandy'). Split on all those, incl. the glued 'and' after a lowercase."""
    parts = re.split(r'\s*,\s*|\s*&\s*|\s+and\s+|(?<=[a-z])and\s+', s or '')
    return [_norm_owner(p) for p in parts if p and p.strip()]


def _surname(name):
    """Last alphabetic token, lowercased -- the stable key for matching an
    owner across name drift (Des/Desmond Foster, Rich/Rexford Landon)."""
    toks = [t for t in re.sub(r"[^A-Za-z ]", ' ', name).split() if len(t) > 1]
    return toks[-1].lower() if toks else name.lower()


def extract_cbs(league_key, league_name):
    """Return (teams, owners, bridge) as lists of dict rows, platform-agnostic
    in column shape."""
    seasons = _cbs_team_seasons(league_key)
    by_id = {}
    for row in seasons:
        by_id.setdefault(row[1], []).append(row)

    franch = {str(r['franchise_id']): r for r in _q(f"""
        select franchise_id, abbrev, franchise_name from cbs_franchises
        where league_key = '{league_key}'""")}
    owner_map = {}   # franchise_id -> [owner_id, ...]
    for r in _q(f"""select franchise_id, owner_id from cbs_team_owners
                    where league_key = '{league_key}'"""):
        owner_map.setdefault(str(r['franchise_id']), []).append(r['owner_id'])
    nick = {r['owner_id']: r for r in _q("""
        select owner_id, first_name, last_name, preferred_name
        from owner_nicknames""")}

    def disp(owner_id):
        n = nick.get(owner_id, {})
        return (n.get('preferred_name')
                or f"{n.get('first_name', '') or ''} "
                   f"{n.get('last_name', '') or ''}".strip()
                or owner_id)

    def span(fid):
        ys = [y for y, _, _ in by_id[fid]]
        return min(ys), max(ys)

    # ---- observed owners per season -------------------------------------
    # stg_cbs__ui_rosters.owner_name is the owner off the year-end roster
    # report header (populated 2007+; blank 2003-06). cbs_ui_parse.py already
    # extracts it -- nothing downstream consumed it until this. This is the
    # real "who owned what when", so most of the bridge is OBSERVED, not
    # guessed; and shared owners across an id break confirm continuity.
    owner_by = {}   # (year, fid) -> [cleaned owner display names]
    for r in _q(f"""select distinct season_year, franchise_id, owner_name
                    from stg_cbs__ui_rosters
                    where league_key = '{league_key}'
                      and owner_name is not null and owner_name <> ''"""):
        owner_by[(int(r['season_year']), str(r['franchise_id']))] = \
            _split_owners(r['owner_name'])
    cur_year = max((y for y, _, _ in seasons), default=None)
    owner_surnames = {}   # fid -> {surname}
    owner_disp = {}       # fid -> {surname: a display name}
    for (yy, ff), ns in owner_by.items():
        for n in ns:
            sn = _surname(n)
            owner_surnames.setdefault(ff, set()).add(sn)
            owner_disp.setdefault(ff, {}).setdefault(sn, n)

    def _owner_link(a, b):
        return owner_surnames.get(a, set()) & owner_surnames.get(b, set())

    # ---- continuity hints + unambiguous-remint suggestions ----
    def norm(s):
        return ''.join(c for c in (s or '').lower() if c.isalnum())

    # identity partners = ids that share a normalized name OR an abbrev
    name_ids = {}
    for fid, rows in by_id.items():
        for _, _, nm in rows:
            name_ids.setdefault(norm(nm), set()).add(fid)
    partners = {fid: set() for fid in by_id}
    for fid in by_id:
        ab = franch.get(fid, {}).get('abbrev')
        for _, _, nm in by_id[fid]:
            partners[fid] |= name_ids.get(norm(nm), set())
        if ab:
            for other, fr in franch.items():
                if other in by_id and fr.get('abbrev') == ab:
                    partners[fid].add(other)
        partners[fid].discard(fid)

    # A later id "claims" an earlier disjoint partner when the sat-out gap is
    # small (<=1) OR they share an owner (surname) -- owner continuity is the
    # strong signal. An earlier id claimed by TWO later ids is a co-owner
    # split (a fork), not a clean lineage: suggest nothing, flag it.
    claims = {}   # earlier_id -> set(later_ids) that would link to it
    for fid in by_id:
        y0, _ = span(fid)
        for p in partners[fid]:
            _, py1 = span(p)
            if py1 < y0 and ((y0 - py1 - 1) <= 1 or _owner_link(fid, p)):
                claims.setdefault(p, set()).add(fid)

    def hint_and_suggest(fid):
        y0, y1 = span(fid)
        msgs, suggest = [], ''
        for p in sorted(partners[fid], key=int):
            py0, py1 = span(p)
            shared = _owner_link(fid, p)
            who = ', '.join(sorted(owner_disp.get(p, {}).get(s)
                                   or owner_disp.get(fid, {}).get(s) or s
                                   for s in shared))
            if py1 < y0:                                   # earlier, disjoint
                gap = y0 - py1 - 1
                fork = len(claims.get(p, ())) > 1
                if fork and shared:
                    rel = (f"co-owned id {p} split in {y0} into "
                           + "+".join(map(str, sorted(claims[p], key=int)))
                           + f" (shared owner {who}) -> partial continuation, "
                           "confirm")
                elif shared:
                    rel = f"SAME owner ({who}) -> almost certainly one franchise"
                elif gap <= 1:
                    rel = f"ended {py1}, {gap or 'no'}-yr gap -> likely continuation"
                else:
                    rel = f"ended {py1}, {gap}-yr gap -> possible revival, confirm"
                if not fork and claims.get(p) == {fid}:
                    suggest = p
            elif py0 > y1:                                 # a newer id
                rel = f"began {py0} after this ended {y1} -> newer id"
            else:                                          # overlap in time
                rel = f"OVERLAPS ({py0}-{py1}) -> coexisted, likely DISTINCT"
                if shared:
                    rel += f" (owner {who} on both -- confirm)"
            msgs.append(f"id {p} ({py0}-{py1}); {rel}")
        return " | ".join(msgs), suggest

    teams = []
    for fid in sorted(by_id, key=int):
        y0, y1 = span(fid)
        hint, suggest = hint_and_suggest(fid)
        latest_name = by_id[fid][-1][2]
        teams.append({
            'Platform': 'CBS', 'League': league_name, 'Team ID': fid,
            'Years': f"{y0}-{y1}",
            'Name History': "  ->  ".join(_name_story(by_id[fid])),
            'Continuity Hint': hint,
            'Same As (Canonical)': suggest,
            'Canonical Name': '', 'Note': '',
            '_latest': latest_name,
        })

    # ---- owners: current-era slugs + everyone ever seen on the pages -----
    def _slug(name):
        return 'cbs-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    cur_disp_to_id = {disp(oid).lower(): oid for oids in owner_map.values()
                      for oid in oids}
    id_name = {}   # owner_id -> display name
    seen_on = {}   # owner_id -> {(fid, year_or_'current')}
    for fid, oids in owner_map.items():
        for oid in oids:
            id_name[oid] = disp(oid)
            seen_on.setdefault(oid, set()).add((fid, 'current'))
    for (yy, ff), ns in owner_by.items():
        for n in ns:                        # reuse a curated slug on exact match
            oid = cur_disp_to_id.get(n.lower()) or _slug(n)
            id_name.setdefault(oid, n)
            seen_on.setdefault(oid, set()).add((ff, yy))
    owners = []
    for oid in sorted(id_name, key=lambda o: id_name[o].lower()):
        by_f = {}
        for f, y in seen_on.get(oid, ()):
            by_f.setdefault(f, []).append(y)
        bits = []
        for f in sorted(by_f, key=int):
            ys = sorted(y for y in by_f[f] if y != 'current')
            bits.append(f"id {f} ({ys[0]}-{ys[-1]})" if ys and ys[0] != ys[-1]
                        else f"id {f} ({ys[0]})" if ys else f"id {f} (current)")
        owners.append({
            'Platform': 'CBS', 'League': league_name, 'Owner ID': oid,
            'Name Seen': id_name[oid], 'Seen On': ", ".join(bits),
            'Same As (Canonical)': '', 'Preferred Name': '', 'Note': '',
        })

    # ---- bridge ----
    # "Owner(s) on record" is the year-end roster header owner (OBSERVED,
    # 2007+); the live season falls back to the curated current owner; pre-2007
    # is blank. The historian only fills Owner 1/2/3 for the blanks / a fix.
    bridge = []
    for fid in sorted(by_id, key=int):
        cur = [disp(o) for o in owner_map.get(fid, [])]
        for y, _, nm in by_id[fid]:
            rec = owner_by.get((y, fid))
            if rec is None and y == cur_year:
                rec = cur
            bridge.append({
                'Platform': 'CBS', 'League': league_name, 'Year': y,
                'Team ID': fid, 'Team Name': nm,
                'Owner(s) on record': "; ".join(rec or []),
                'Owner 1': '', 'Owner 2': '', 'Owner 3': '', 'Note': '',
            })
    return teams, owners, bridge


_ADAPTERS = {'cbs': extract_cbs}


# --------------------------------------------------------------------------
# Tab specifications: column order + which columns are read-only (grey) vs
# fill-or-ignore (yellow). Anything not listed grey is treated as fillable.
# --------------------------------------------------------------------------
_TABS = {
    'Teams': {
        'purpose': ("One row per team ID. Mark which IDs are really the same "
                    "club (Same As), and its preferred name (Canonical Name). "
                    "Blank = this team stands alone / observed name is fine."),
        'cols': ['Platform', 'League', 'Team ID', 'Years', 'Name History',
                 'Continuity Hint', 'Same As (Canonical)', 'Canonical Name',
                 'Note'],
        'grey': ['Platform', 'League', 'Team ID', 'Years', 'Name History',
                 'Continuity Hint'],
        'widths': {'Name History': 430, 'Continuity Hint': 300,
                   'Years': 90, 'Same As (Canonical)': 130,
                   'Canonical Name': 160, 'Note': 220},
        'dropdown': {'Same As (Canonical)': 'Teams_ids'},
    },
    'Owners': {
        'purpose': ("One row per owner handle. Merge duplicate handles for the "
                    "same person (Same As), and set a Preferred Name. Blank = "
                    "unique person / name seen is fine."),
        'cols': ['Platform', 'League', 'Owner ID', 'Name Seen', 'Seen On',
                 'Same As (Canonical)', 'Preferred Name', 'Note'],
        'grey': ['Platform', 'League', 'Owner ID', 'Name Seen', 'Seen On'],
        'widths': {'Owner ID': 170, 'Seen On': 240,
                   'Same As (Canonical)': 150, 'Preferred Name': 160,
                   'Note': 220},
        'dropdown': {'Same As (Canonical)': 'Owners_ids'},
    },
    'Team-Owner by Year': {
        'purpose': ("One row per team per year. 'Owner(s) on record' is the "
                    "OWNER OFF THE YEAR-END ROSTER PAGE (2007+, blank before "
                    "that). Only fill Owner 1/2/3 to fix a wrong one or fill a "
                    "pre-2007 blank; leave blank to accept the record."),
        'cols': ['Platform', 'League', 'Year', 'Team ID', 'Team Name',
                 'Owner(s) on record', 'Owner 1', 'Owner 2', 'Owner 3', 'Note'],
        'grey': ['Platform', 'League', 'Year', 'Team ID', 'Team Name',
                 'Owner(s) on record'],
        'widths': {'Team Name': 210, 'Owner(s) on record': 190, 'Owner 1': 150,
                   'Owner 2': 150, 'Owner 3': 150, 'Note': 170},
        'dropdown': {'Owner 1': 'Owners_names', 'Owner 2': 'Owners_names',
                     'Owner 3': 'Owners_names'},
    },
}

_TAB_ORDER = ['READ ME FIRST', 'Teams', 'Owners', 'Team-Owner by Year']

_READ_ME = [
    ["League Continuity Mapping"],
    [""],
    ["What this is: a place to record the things the fantasy platform can't "
     "tell us on its own -- which teams are really the same club across "
     "renames, which owner logins are the same person, and who owned what "
     "when. Fill in what you know; leave the rest blank."],
    [""],
    ["THE ONE RULE: a blank is always OK. Blank means \"the greyed-in value is "
     "already correct.\" You only ever type to CORRECT something or to LINK two "
     "rows together."],
    [""],
    ["Colours:"],
    ["   GREY cells", "= what the data already tells us. Read-only; please "
     "don't retype them (the sheet will warn you if you do)."],
    ["   YELLOW cells", "= yours to fill or ignore."],
    [""],
    ["Tab \"Teams\" -- one row per team:"],
    ["   * If a team was renamed but kept the same ID, nothing to do -- the "
     "Name History already shows the whole story."],
    ["   * If two DIFFERENT IDs are really the same club (a team left and came "
     "back under a new ID), put the EARLIER ID number in 'Same As' on the "
     "newer row. The 'Continuity Hint' points out likely cases."],
    ["   * 'Canonical Name' = what we should call the club today (leave blank "
     "to use its most recent name)."],
    [""],
    ["Tab \"Owners\" -- one row per owner login:"],
    ["   * If one person had two logins, put the preferred Owner ID in 'Same "
     "As' on the duplicate. 'Preferred Name' overrides how the name shows."],
    [""],
    ["Tab \"Team-Owner by Year\" -- who owned each team each year:"],
    ["   * 'Owner(s) on record' is already filled from the year-end roster "
     "pages (2007 onward) -- so most of this tab is DONE. It's blank only "
     "before 2007. Fill Owner 1/2/3 just to correct a wrong one or fill an "
     "early blank; one name per column for co-owned teams. The team's name "
     "that year is shown to jog your memory."],
    [""],
    ["Yellow cells are DROPDOWNS -- click the little arrow and pick a name/ID "
     "from the list. If who you need isn't there yet (an owner from the early "
     "years we haven't seen), just type it in; that's expected."],
    [""],
    ["Advanced -- linking across leagues: if this club (or person) also exists "
     "in another league's copy of this sheet, type the SAME made-up label in "
     "'Same As' on both, and the two histories stitch into one."],
]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
_GREY = {'red': 0.937, 'green': 0.937, 'blue': 0.937}
_YELLOW = {'red': 1.0, 'green': 0.976, 'blue': 0.878}
_HEADER_BG = {'red': 0.196, 'green': 0.263, 'blue': 0.361}
_WHITE = {'red': 1, 'green': 1, 'blue': 1}


def _rows_for(tab, data):
    spec = _TABS[tab]
    cols = spec['cols']
    header = cols
    body = [[('' if r.get(c, '') is None else r.get(c, '')) for c in cols]
            for r in data]
    return [[spec['purpose']] + [''] * (len(cols) - 1), header] + body


def _col_letter(i):
    return gspread.utils.rowcol_to_a1(1, i + 1).rstrip('1')


def _style_requests(gid, tab, n_rows):
    spec = _TABS[tab]
    cols = spec['cols']
    reqs = [
        {'repeatCell': {'range': {'sheetId': gid}, 'cell': {},
                        'fields': 'userEnteredFormat'}},
        {'updateSheetProperties': {
            'properties': {'sheetId': gid,
                           'gridProperties': {'frozenRowCount': 2}},
            'fields': 'gridProperties.frozenRowCount'}},
        # purpose row
        {'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': 0, 'endRowIndex': 1},
            'cell': {'userEnteredFormat': {
                'textFormat': {'italic': True,
                               'foregroundColor': {'red': .4, 'green': .4,
                                                   'blue': .4}},
                'wrapStrategy': 'WRAP'}},
            'fields': 'userEnteredFormat(textFormat,wrapStrategy)'}},
        # header row
        {'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': 1, 'endRowIndex': 2},
            'cell': {'userEnteredFormat': {
                'backgroundColor': _HEADER_BG,
                'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                'horizontalAlignment': 'CENTER', 'wrapStrategy': 'WRAP'}},
            'fields': 'userEnteredFormat(backgroundColor,textFormat,'
                      'horizontalAlignment,wrapStrategy)'}},
    ]
    # column body fills (grey vs yellow), below the 2 header rows
    for i, c in enumerate(cols):
        colour = _GREY if c in spec['grey'] else _YELLOW
        reqs.append({'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': 2,
                      'startColumnIndex': i, 'endColumnIndex': i + 1},
            'cell': {'userEnteredFormat': {'backgroundColor': colour}},
            'fields': 'userEnteredFormat.backgroundColor'}})
    # widths
    for i, c in enumerate(cols):
        w = spec['widths'].get(c, 95)
        reqs.append({'updateDimensionProperties': {
            'range': {'sheetId': gid, 'dimension': 'COLUMNS',
                      'startIndex': i, 'endIndex': i + 1},
            'properties': {'pixelSize': w}, 'fields': 'pixelSize'}})
    return reqs


def _protect_requests(gid, tab):
    """warningOnly protection over the grey (read-only) columns -- soft, so a
    non-technical filler is nudged, never blocked."""
    spec = _TABS[tab]
    reqs = []
    for i, c in enumerate(spec['cols']):
        if c in spec['grey']:
            reqs.append({'addProtectedRange': {'protectedRange': {
                'range': {'sheetId': gid, 'startRowIndex': 2,
                          'startColumnIndex': i, 'endColumnIndex': i + 1},
                'warningOnly': True,
                'description': 'Observed data - please do not edit'}}})
    return reqs


# Fill columns become dropdowns so the historian picks from a known list
# instead of free-typing (which no downstream parser could trust). The lists
# live in other tabs, referenced by range. strict=False + showCustomUi: a
# value not in the list (a never-before-seen historical owner, or a made-up
# cross-league stitch label) is still allowed -- the dropdown suggests, it
# doesn't imprison.
_DROPDOWN_RANGES = {
    'Teams_ids':    '=Teams!$C$3:$C$1000',     # Teams.Team ID
    'Owners_ids':   '=Owners!$C$3:$C$1000',    # Owners.Owner ID
    'Owners_names': '=Owners!$D$3:$D$1000',    # Owners.Name Seen
}


def _validation_requests(gid, tab):
    spec = _TABS[tab]
    reqs = []
    for i, c in enumerate(spec['cols']):
        src = spec.get('dropdown', {}).get(c)
        if not src:
            continue
        reqs.append({'setDataValidation': {
            'range': {'sheetId': gid, 'startRowIndex': 2,
                      'startColumnIndex': i, 'endColumnIndex': i + 1},
            'rule': {
                'condition': {'type': 'ONE_OF_RANGE',
                              'values': [{'userEnteredValue':
                                          _DROPDOWN_RANGES[src]}]},
                'strict': False, 'showCustomUi': True}}})
    return reqs


def _readme_requests(gid):
    return [
        {'repeatCell': {'range': {'sheetId': gid}, 'cell': {},
                        'fields': 'userEnteredFormat'}},
        {'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': 0, 'endRowIndex': 1},
            'cell': {'userEnteredFormat': {
                'textFormat': {'bold': True, 'fontSize': 14}}},
            'fields': 'userEnteredFormat.textFormat'}},
        {'updateDimensionProperties': {
            'range': {'sheetId': gid, 'dimension': 'COLUMNS',
                      'startIndex': 0, 'endIndex': 1},
            'properties': {'pixelSize': 180}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {
            'range': {'sheetId': gid, 'dimension': 'COLUMNS',
                      'startIndex': 1, 'endIndex': 2},
            'properties': {'pixelSize': 760}, 'fields': 'pixelSize'}},
    ]


# --------------------------------------------------------------------------
# Preview + write drivers
# --------------------------------------------------------------------------
def _write_preview(preview_dir, teams, owners, bridge):
    Path(preview_dir).mkdir(parents=True, exist_ok=True)
    for tab, data in [('Teams', teams), ('Owners', owners),
                      ('Team-Owner by Year', bridge)]:
        cols = _TABS[tab]['cols']
        path = Path(preview_dir) / f"{tab.replace(' ', '_')}.tsv"
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write('\t'.join(cols) + '\n')
            for r in data:
                f.write('\t'.join(str(r.get(c, '') or '') for c in cols) + '\n')
        print(f"  wrote {path}  ({len(data)} rows)")


def _reset_protected_ranges(spreadsheet):
    meta = _retry('fetch metadata', spreadsheet.fetch_sheet_metadata)
    reqs = []
    for sh in meta.get('sheets', []):
        for pr in sh.get('protectedRanges', []) or []:
            reqs.append({'deleteProtectedRange':
                         {'protectedRangeId': pr['protectedRangeId']}})
    if reqs:
        _retry('clear protections',
               lambda: spreadsheet.batch_update({'requests': reqs}))


def _ensure_tab(spreadsheet, title, n_rows, n_cols):
    need_rows, need_cols = max(n_rows + 10, 40), max(n_cols, 8)
    try:
        ws = spreadsheet.worksheet(title)
        # A rerun may need more room than the prior grid (e.g. the bridge grew
        # a column, or a new season added rows) -- grow, never shrink.
        if ws.row_count < need_rows or ws.col_count < need_cols:
            _retry(f'resize {title}',
                   lambda: ws.resize(rows=max(ws.row_count, need_rows),
                                     cols=max(ws.col_count, need_cols)))
    except gspread.WorksheetNotFound:
        ws = _retry(f'create {title}',
                    lambda: spreadsheet.add_worksheet(
                        title=title, rows=need_rows, cols=need_cols))
    _retry(f'clear {title}', ws.clear)
    return ws


def _write_sheet(sheet_id, teams, owners, bridge):
    client = _get_authorized_client()
    ss = _retry('open', lambda: client.open_by_key(sheet_id))
    _reset_protected_ranges(ss)

    # READ ME (pad to a rectangular 2-col block)
    readme = [row + [''] * (2 - len(row)) for row in _READ_ME]
    ws = _ensure_tab(ss, 'READ ME FIRST', len(readme), 2)
    _retry('update READ ME',
           lambda: ws.update(readme, 'A1', value_input_option='RAW'))
    _retry('style READ ME',
           lambda: ss.batch_update({'requests': _readme_requests(ws.id)}))
    print("  wrote READ ME FIRST")

    for tab, data in [('Teams', teams), ('Owners', owners),
                      ('Team-Owner by Year', bridge)]:
        rows = _rows_for(tab, data)
        ncol = len(_TABS[tab]['cols'])
        ws = _ensure_tab(ss, tab, len(rows), ncol)
        _retry(f'update {tab}',
               lambda ws=ws, rows=rows: ws.update(
                   rows, 'A1', value_input_option='USER_ENTERED'))
        _retry(f'style {tab}', lambda ws=ws, tab=tab, data=data: ss.batch_update(
            {'requests': _style_requests(ws.id, tab, len(data))}))
        _retry(f'protect {tab}', lambda ws=ws, tab=tab: ss.batch_update(
            {'requests': _protect_requests(ws.id, tab)}))
        vreqs = _validation_requests(ws.id, tab)
        if vreqs:
            _retry(f'validate {tab}',
                   lambda vreqs=vreqs: ss.batch_update({'requests': vreqs}))
        print(f"  wrote {tab}  ({len(data)} rows)")

    # order + drop any leftover default sheet
    by_title = {ws.title: ws for ws in _retry('list tabs', ss.worksheets)}
    reqs = []
    for idx, title in enumerate(_TAB_ORDER):
        if title in by_title:
            reqs.append({'updateSheetProperties': {
                'properties': {'sheetId': by_title[title].id, 'index': idx},
                'fields': 'index'}})
    if reqs:
        _retry('sort tabs',
               lambda: ss.batch_update({'requests': reqs}))
    for title, ws in by_title.items():
        if title not in _TAB_ORDER:
            try:
                _retry('drop default', lambda ws=ws: ss.del_worksheet(ws))
            except gspread.exceptions.APIError:
                pass
    print(f"\nDone: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")


def _sheet_id_from(arg):
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', arg or '')
    return m.group(1) if m else arg


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--league', required=True,
                    help="league_key, e.g. cbs-bsb")
    ap.add_argument('--sheet-id', help="target Google Sheet id or full url")
    ap.add_argument('--preview-dir',
                    help="write TSV previews here and DO NOT touch Sheets")
    args = ap.parse_args()

    db.init()
    db.set_league(args.league)
    league = db.league()
    platform = getattr(league, 'platform', None) or args.league.split('-')[0]
    league_name = getattr(league, 'display_name', None) or args.league

    adapter = _ADAPTERS.get(platform)
    if adapter is None:
        sys.exit(f"No continuity adapter for platform {platform!r}. "
                 f"Wired: {sorted(_ADAPTERS)}.")
    teams, owners, bridge = adapter(args.league, league_name)
    print(f"[continuity] {args.league}: {len(teams)} teams, "
          f"{len(owners)} owners, {len(bridge)} team-seasons")

    if args.preview_dir:
        _write_preview(args.preview_dir, teams, owners, bridge)
        return
    if not args.sheet_id:
        sys.exit("Pass --sheet-id <id-or-url> to write, or --preview-dir to "
                 "preview without touching Sheets.")
    _write_sheet(_sheet_id_from(args.sheet_id), teams, owners, bridge)


if __name__ == '__main__':
    main()
