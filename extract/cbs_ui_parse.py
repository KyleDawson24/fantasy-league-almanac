"""cbs_ui_parse.py -- captured CBS UI history pages -> structured RAW rows.

The parse half of the MLB-47 capture: the league-history pages hold the
fantasy-layer history the API denies (year-end rosters, era standings,
transaction logs), archived as verbatim HTML under
data/cbs_raw/bsb/history/ui/. This script turns them into structured rows
and lands them in RAW tables, family by family, mirroring the
cbs_load.py mechanics (NDJSON -> PUT -> COPY, idempotent by source_path).

Families -> tables:
  rosters    (MLB-55)  ui/rosters/{year}/team_{id}.html -> CBS_UI_ROSTERS
             One row per player on a team's YEAR-END roster: the anchor
             states the MLB-63 walk-back reconstruction starts from.
             The <table id=lineup_views_archived> is clean and regular:
             a title row "Team Name - Owner Name", a label row, then one
             row per player ("Last, First POS MLBTEAM" | MLB status |
             Own % | Start % | A/RS | deployed Pos). The page also embeds
             a global player-picker (the MLB-55 furniture warning) --
             everything outside lineup_views_archived is ignored.

Parsing is stdlib-regex on purpose: the tables are machine-generated and
rigidly regular, and the archive is immutable (re-verify against the
verbatim HTML any time). No CBS player ids exist on these pages --
identity resolution (name+season+team -> MLBAM) is a separate pass, NOT
this script's job; rows land with the name evidence verbatim.

Usage:
  py extract/cbs_ui_parse.py                    # parse + land everything new
  py extract/cbs_ui_parse.py --dry-run          # parse + report, land nothing
  py extract/cbs_ui_parse.py --force            # re-parse + reload all
"""

import argparse
import html as htmllib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

LEAGUE_KEY = "cbs-bsb"

TABLES = {
    "CBS_UI_TRANSACTIONS": """CREATE TABLE IF NOT EXISTS CBS_UI_TRANSACTIONS (
        league_key          VARCHAR,
        season_year         INTEGER,
        txn_ts_raw          VARCHAR,
        effective_date_raw  VARCHAR,
        team_id             VARCHAR,
        team_name           VARCHAR,
        player_cbs_id       VARCHAR,
        player_name_raw     VARCHAR,
        player_pos_team_raw VARCHAR,
        action_raw          VARCHAR,
        txn_row_key         VARCHAR,
        row_seq             INTEGER,
        entry_seq           INTEGER,
        source_path         VARCHAR,
        loaded_at           TIMESTAMP_NTZ
    )""",
    "CBS_UI_STANDINGS": """CREATE TABLE IF NOT EXISTS CBS_UI_STANDINGS (
        league_key      VARCHAR,
        season_year     INTEGER,
        division_name   VARCHAR,
        standings_rank  INTEGER,
        franchise_id    INTEGER,
        team_name       VARCHAR,
        batting_points  FLOAT,
        pitching_points FLOAT,
        total_points    FLOAT,
        points_behind   FLOAT,
        source_path     VARCHAR,
        loaded_at       TIMESTAMP_NTZ
    )""",
    "CBS_UI_ROSTERS": """CREATE TABLE IF NOT EXISTS CBS_UI_ROSTERS (
        league_key      VARCHAR,
        season_year     INTEGER,
        franchise_id    INTEGER,
        team_name       VARCHAR,
        owner_name      VARCHAR,
        player_name_raw VARCHAR,
        player_name     VARCHAR,
        primary_pos     VARCHAR,
        mlb_team        VARCHAR,
        mlb_status      VARCHAR,
        own_pct         INTEGER,
        start_pct       INTEGER,
        roster_status   VARCHAR,
        roster_pos      VARCHAR,
        eligible_pos    VARCHAR,
        source_path     VARCHAR,
        loaded_at       TIMESTAMP_NTZ
    )""",
    # MLB-90: the draft picks the Draft Recap tab has been reading straight
    # off disk. Unlike the families above, the HTML is already parsed --
    # cbs_draft_parse.py wrote draft_rows.ndjson as the evidence layer,
    # explicitly waiting on the modeling shape to be signed off. So the
    # walker below re-reads that NDJSON rather than re-parsing pages, and
    # RAW takes it VERBATIM: both the round and team views of each draft,
    # playerless order-only rows included. Selecting one view per season and
    # dropping the furniture is staging's job, exactly as it is everywhere
    # else in this file.
    "CBS_DRAFT": """CREATE TABLE IF NOT EXISTS CBS_DRAFT (
        league_key      VARCHAR,
        season_year     INTEGER,
        draft_key       VARCHAR,
        draft_label     VARCHAR,
        period          VARCHAR,
        period_order    INTEGER,
        view            VARCHAR,
        section_seq     INTEGER,
        section_kind    VARCHAR,
        section_label   VARCHAR,
        row_seq         INTEGER,
        page_seq        INTEGER,
        pick_no         INTEGER,
        round_num       INTEGER,
        round_pick      INTEGER,
        team_name_raw   VARCHAR,
        player_cbs_id   VARCHAR,
        player_name_raw VARCHAR,
        pos_team_raw    VARCHAR,
        elig_raw        VARCHAR,
        salary_raw      VARCHAR,
        elapsed_raw     VARCHAR,
        rank_raw        VARCHAR,
        total_fpts      FLOAT,
        active_fpts     FLOAT,
        is_playerless   BOOLEAN,
        parsed_at       VARCHAR,
        source_path     VARCHAR,
        loaded_at       TIMESTAMP_NTZ
    )""",
}

_TABLE_RE = re.compile(
    r'<table[^>]*id=lineup_views_archived.*?</table>', re.DOTALL)
_ANY_TABLE_RE = re.compile(r'<table[^>]*>.*?</table>', re.DOTALL)
_CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)


def _roster_table_region(html, rel_path):
    """The roster content spans ONE OR MANY tables depending on the render:
    the league-wide 2012+ pages put every team inside the single
    id=lineup_views_archived table, while the (re-captured 2026-07-12)
    early-year pages render team 1 in the id'd table and teams 2..N in
    consecutive sibling <table class="data"> blocks right after it. Take
    the contiguous run: the id'd table plus every immediately-following
    data table that opens with a title row. The page's OTHER furniture
    (the global player-picker) doesn't match that shape and ends the run."""
    tables = list(_ANY_TABLE_RE.finditer(html))
    start = next((n for n, m in enumerate(tables)
                  if 'lineup_views_archived' in m.group(0)[:200]), None)
    if start is None:
        raise ValueError(f"{rel_path}: no lineup_views_archived table")
    chunks = [tables[start].group(0)]
    for m in tables[start + 1:]:
        head = m.group(0)[:300]
        if 'class="data"' in head and '<tr class="title">' in head:
            chunks.append(m.group(0))
        else:
            break
    return "\n".join(chunks)

# Label rows name the columns, and BOTH the column set and the first
# cell's text are era-dependent: the modern era runs Player | MLB |
# Own % | Start % | Status | Pos; the early era Player | MLB | Status |
# Pos | Eligible (no ownership percentages, but an eligibility list the
# later era lacks); the mid era SECTIONS the table with repeated label
# rows whose first cell names the section ("Active Batters", ...). So
# parsing walks the <tr>s in order, re-templating on every label row,
# and the FIRST label cell is always the player column whatever its
# text.
_LABEL_KEYS = {
    'mlb': 'mlb',
    'own': 'own',
    'own %': 'own',
    'start': 'start',
    'start %': 'start',
    'status': 'status',
    'pos': 'pos',
    'eligible': 'eligible',
}
_TR_RE = re.compile(
    r'<tr class="(title|label|row[12])"[^>]*>(.*?)</tr>', re.DOTALL)


def _clean(cell):
    """Strip tags/entities/whitespace from one table cell."""
    text = re.sub(r'<[^>]+>', '', cell)
    return htmllib.unescape(text).strip()


def _split_player_raw(raw):
    """'Sanchez, Gary C NYY' -> ('Gary Sanchez', 'C', 'NYY'). The MLB team
    and position are the last two whitespace tokens; everything before is
    the 'Last, First' name (suffixes ride the last name: 'Guerrero Jr.,
    Vladimir' -> 'Vladimir Guerrero Jr.')."""
    tokens = raw.split()
    if len(tokens) < 3:
        return raw, None, None
    mlb_team = tokens[-1]
    pos = tokens[-2]
    last_first = ' '.join(tokens[:-2])
    if ', ' in last_first:
        last, first = last_first.split(', ', 1)
        name = f"{first} {last}"
    else:
        name = last_first
    return name, pos, mlb_team


def _pct(value):
    value = value.replace('%', '').strip()
    try:
        return int(value)
    except ValueError:
        return None


def parse_roster_page(path, rel_path):
    """One rosters/{year}/team_{id}.html -> (rows, multi_team).

    TWO PAGE SHAPES exist in the archive:
      - Early era (2003-2011): the page holds ONE team's archived lineup;
        the filename's team_{id} is that team's franchise id.
      - Modern era (2012+): the SAME URL renders the WHOLE LEAGUE -- one
        lineup_views_archived table holding every team's section, each
        introduced by a title row ("Team Name - Owner Name"). Sibling
        team_{id} files within a year are duplicates of this league-wide
        view, and the filename id is meaningless for section attribution,
        so franchise_id lands NULL (resolved later by joining the
        standings parse's per-season name -> id map).
    """
    year = int(path.parent.name)
    file_franchise_id = int(re.search(r'team_(\d+)\.html$', path.name).group(1))
    html = path.read_text(encoding='utf-8', errors='replace')

    table = _roster_table_region(html, rel_path)

    rows = []
    labels = None
    team_name, owner_name = None, None
    title_count = 0
    for tr_match in _TR_RE.finditer(table):
        kind, body = tr_match.group(1), tr_match.group(2)
        cells = [_clean(c) for c in _CELL_RE.findall(body)]
        if kind == 'title':
            title_count += 1
            labels = None
            title = cells[0] if cells else ''
            # "Team Name - Owner Name" (owner absent in the early era);
            # team names may contain dashes, CBS's separator is ' - '
            # with spaces -- split on the LAST one.
            if ' - ' in title:
                team_name, owner_name = title.rsplit(' - ', 1)
            else:
                team_name, owner_name = title, None
            continue
        if kind == 'label':
            labels = ['player'] + [
                _LABEL_KEYS.get(c.lower().strip()) for c in cells[1:]]
            if 'status' not in labels:
                # Not a column template (e.g. the trailing 'TOTALS'
                # summary row is label-classed): close the section so
                # stray rows after it can't mis-zip.
                labels = None
            continue
        if labels is None or len(cells) != len(labels):
            continue
        cell = dict(zip(labels, cells))
        raw = cell.get('player', '')
        if not raw:
            continue
        name, primary_pos, mlb_team = _split_player_raw(raw)
        rows.append({
            "league_key": LEAGUE_KEY,
            "season_year": year,
            "franchise_id": None,   # filename id backfilled below when single-team
            "team_name": team_name,
            "owner_name": owner_name,
            "player_name_raw": raw,
            "player_name": name,
            "primary_pos": primary_pos,
            "mlb_team": mlb_team,
            "mlb_status": cell.get('mlb') or None,
            "own_pct": _pct(cell.get('own', '')),
            "start_pct": _pct(cell.get('start', '')),
            "roster_status": cell.get('status') or None,
            "roster_pos": cell.get('pos') or None,
            "eligible_pos": cell.get('eligible') or None,
            "source_path": rel_path,
        })
    if not rows:
        raise ValueError(f"{rel_path}: table matched but zero player rows")
    multi_team = title_count > 1
    if not multi_team:
        for row in rows:
            row["franchise_id"] = file_franchise_id
    return rows, multi_team


def _roster_row_key(row):
    """Content identity of one roster row (source_path excluded), for the
    duplicate-league-view consistency check."""
    return (row["season_year"], row["team_name"], row["player_name_raw"],
            row["roster_status"], row["roster_pos"])


def walk_rosters(ui_root):
    root = ui_root / "rosters"
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir():
            continue
        paths = sorted(year_dir.glob("team_*.html"))
        if not paths:
            continue
        parsed = []
        for path in paths:
            rel = str(path.relative_to(ui_root)).replace("\\", "/")
            rows, multi_team = parse_roster_page(path, rel)
            parsed.append((rel, rows, multi_team))
        if parsed[0][2]:
            # Modern era: every file is the same league-wide view. Emit
            # the first, verify the siblings carry the identical row set
            # (a divergence would mean the assumption is wrong -- warn
            # loudly, still emit only the first).
            first_rel, first_rows, _ = parsed[0]
            first_keys = {_roster_row_key(r) for r in first_rows}
            for rel, rows, _ in parsed[1:]:
                if {_roster_row_key(r) for r in rows} != first_keys:
                    print(f"  WARNING {rel}: league-wide roster view "
                          f"differs from {first_rel}; emitting {first_rel} "
                          f"only -- inspect before trusting {year_dir.name}")
            print(f"  {year_dir.name}: league-wide page; "
                  f"{len(parsed) - 1} sibling files are duplicates")
            for row in first_rows:
                yield "CBS_UI_ROSTERS", row
        else:
            # Early era: single-team pages. DISCOVERED 2026-07-12: for
            # 2003-2011 CBS ignores the team_{id} URL parameter and serves
            # the same (commissioner's) roster for every id -- the files
            # differ only in page furniture. When every file in a year
            # parses to the identical row set, emit ONE copy with
            # franchise_id NULL (the filename id is a lie; the name->id
            # map resolves it later) and say so loudly. If files genuinely
            # differ (the healthy case), emit each under its filename id.
            keysets = {rel: {_roster_row_key(r) for r in rows}
                       for rel, rows, _ in parsed}
            if len(parsed) > 1 and len(set(map(frozenset, keysets.values()))) == 1:
                first_rel, first_rows, _ = parsed[0]
                team = first_rows[0]["team_name"]
                print(f"  WARNING {year_dir.name}: all {len(parsed)} "
                      f"single-team files carry the SAME roster "
                      f"({team!r}) -- CBS ignored the team id; emitting "
                      f"one copy, franchise unresolved by id")
                for row in first_rows:
                    row["franchise_id"] = None
                    yield "CBS_UI_ROSTERS", row
            else:
                for rel, rows, _ in parsed:
                    for row in rows:
                        yield "CBS_UI_ROSTERS", row


# ---------------------------------------------------------------------------
# standings (MLB-53): ui/standings/{year}.html -> CBS_UI_STANDINGS.
# The year-by-year dashboard's "Final Standings" card: divisions as
# subtitle rows, label rows in <th> cells (Rank | Team | Batting |
# Pitching | Total | Behind -- column set label-driven for era drift),
# and each team cell links /history/team-overview/{franchise_id} -- the
# per-season name -> franchise id map every other UI family joins on.
# ---------------------------------------------------------------------------

_STANDINGS_TR_RE = re.compile(
    r'<tr\s+class="(subtitle|label|row[12])"[^>]*>(.*?)</tr>', re.DOTALL)
_ANY_CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.DOTALL)
_TEAM_LINK_RE = re.compile(
    r"history/team-overview/(\d+)'[^>]*>([^<]+)<")
_STANDINGS_LABEL_KEYS = {
    'rank': 'rank',
    'team': 'team',
    'batting': 'batting',
    'pitching': 'pitching',
    'total': 'total',
    'behind': 'behind',
    'points': 'total',   # era variant: a single Points column
}


def _num(value):
    value = value.replace(',', '').strip()
    try:
        return float(value)
    except ValueError:
        return None


def parse_standings_page(path, rel_path):
    """One standings/{year}.html -> list of row dicts."""
    year = int(path.stem)
    html = path.read_text(encoding='utf-8', errors='replace')

    i = html.find('>Final Standings<')
    if i < 0:
        raise ValueError(f"{rel_path}: no Final Standings card")
    table_start = html.find('<table', i)
    table_end = html.find('</table>', table_start)
    table = html[table_start:table_end]

    rows = []
    labels = None
    division = None
    for tr_match in _STANDINGS_TR_RE.finditer(table):
        kind, body = tr_match.group(1), tr_match.group(2)
        if kind == 'subtitle':
            division = _clean(_ANY_CELL_RE.search(body).group(1))
            continue
        if kind == 'label':
            labels = [_STANDINGS_LABEL_KEYS.get(_clean(c).lower())
                      for c in _ANY_CELL_RE.findall(body)]
            if 'rank' not in labels or 'team' not in labels:
                labels = None
            continue
        if labels is None:
            continue
        cells = _ANY_CELL_RE.findall(body)
        if len(cells) != len(labels):
            continue
        cell = dict(zip(labels, cells))
        team_cell = cell.get('team', '')
        link = _TEAM_LINK_RE.search(team_cell)
        rows.append({
            "league_key": LEAGUE_KEY,
            "season_year": year,
            "division_name": division,
            "standings_rank": int(_clean(cell.get('rank', '')) or 0) or None,
            "franchise_id": int(link.group(1)) if link else None,
            "team_name": (htmllib.unescape(link.group(2)).strip() if link
                          else _clean(team_cell)),
            "batting_points": _num(_clean(cell.get('batting', ''))),
            "pitching_points": _num(_clean(cell.get('pitching', ''))),
            "total_points": _num(_clean(cell.get('total', ''))),
            "points_behind": _num(_clean(cell.get('behind', ''))),
            "source_path": rel_path,
        })
    if not rows:
        # The in-progress season's card is legitimately empty (the year
        # has no FINAL standings yet; the API's period standings carry
        # the live season). A structural change would fail earlier at
        # the missing-card check.
        print(f"  {rel_path}: Final Standings card empty "
              f"(season in progress) -- skipped")
    return rows


def walk_standings(ui_root):
    root = ui_root / "standings"
    for path in sorted(root.glob("*.html")):
        rel = str(path.relative_to(ui_root)).replace("\\", "/")
        for row in parse_standings_page(path, rel):
            yield "CBS_UI_STANDINGS", row


# ---------------------------------------------------------------------------
# transactions (MLB-54): ui/transactions/all/{year}[_r{N}].html ->
# CBS_UI_TRANSACTIONS. One output row per PLAYER-ACTION (a transaction row
# can carry several: an add+drop pair, a multi-player lineup change).
# Rows land STRUCTURED-VERBATIM -- timestamps, effective dates, and the
# era's action phrase land as raw strings; normalization (verb vocabulary
# -> move types, date parsing, name flips) is staging's job.
#
# Era shapes handled:
#   2001-2003: 4 cells = date | team name | 'Last, First' | ACTION PHRASE
#     ('Reserved', 'Activated', 'Signed', 'Released', 'Moved from DH to
#     1B'). One player per row, no links, no effective date.
#   2004-2015: date-time | team (name; linked with /teams/{id} from ~2013)
#     | players cell | effective date. Entries packed 'Last, First POS TEAM
#     - Added' separated by <br> in early years; from ~2013 players are
#     playerpage links ('First Last') with a pos/team span, and 2015 drops
#     the <br> separators (entries split on text-bearing player anchors).
#     Trades read 'Traded from {Team}' ('Trades from' in 2008).
#   2021+: verbs may sit in a commish span; injury-icon anchors (linked
#     spans with no text) are furniture and are stripped.
# ---------------------------------------------------------------------------

# Transaction rows are matched by EXCLUSION: every <tr> in the data table
# except the label header and footer/pagination furniture. The zebra
# classes are NOT a reliable include-list -- CBS renders some real
# transaction rows as class="bgFan" (an ad-adjacent styling variant), and
# missing those is exactly the off-by-one that made the capture's offset
# stride overlap every page boundary. CBS also occasionally renders the
# SAME transaction twice in adjacent rows (differing only in zebra
# class), so row identity is the hash of the CELL CONTENTS, class
# ignored.
_TXN_ANY_TR_RE = re.compile(r'<tr([^>]*)>(.*?)</tr>', re.DOTALL)
_TXN_SKIP_CLASS_RE = re.compile(r'class="(?:label|footer)')
_TXN_TR_ID_RE = re.compile(r'id="([^"]*)"')
_TEAM_LINK_TXN_RE = re.compile(r"/teams/(\d+)\b")
_PLAYER_ANCHOR_RE = re.compile(
    r"<a[^>]*playerpage/(\d+)[^>]*>([^<]+)</a>")
_POS_TEAM_SPAN_RE = re.compile(
    r'<span class="playerPositionAndTeam">([^<]*)</span>')
_EFFECTIVE_RE = re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}$')


def _txn_entries(players_cell):
    """Split one players cell into per-player segments (era-tolerant)."""
    # Injury-icon furniture: playerpage anchors whose text is markup-only.
    cell = re.sub(r'<a[^>]*playerpage/\d+[^>]*>\s*(?:<[^>]+>\s*)*</a>', '',
                  players_cell)
    anchors = list(_PLAYER_ANCHOR_RE.finditer(cell))
    if anchors:
        segments = []
        for n, m in enumerate(anchors):
            end = anchors[n + 1].start() if n + 1 < len(anchors) else len(cell)
            segments.append((m.group(1), m.group(2).strip(),
                             cell[m.start():end]))
        return segments
    if '<br>' in cell.lower() or '<br/>' in cell.lower() or '<br />' in cell.lower():
        parts = re.split(r'<br\s*/?>', cell, flags=re.IGNORECASE)
        return [(None, None, p) for p in parts if _clean(p)]
    return [(None, None, cell)] if _clean(cell) else []


def parse_transactions_page(path, rel_path, year):
    html = path.read_text(encoding='utf-8', errors='replace')
    i = html.find('class="data borderTop')
    if i < 0:
        raise ValueError(f"{rel_path}: no transaction data table")
    table = html[i:html.find('</table>', i)]

    rows = []
    prev_cells_hash = None
    for row_match in _TXN_ANY_TR_RE.finditer(table):
        attrs, body = row_match.group(1), row_match.group(2)
        if _TXN_SKIP_CLASS_RE.search(attrs):
            continue
        id_match = _TXN_TR_ID_RE.search(attrs)
        txn_key = id_match.group(1) if id_match else None
        cells = re.findall(r'<td[^>]*>(.*?)</td>', body, re.DOTALL)
        if len(cells) < 4:
            continue
        row_hash = hash(tuple(cells))
        if row_hash == prev_cells_hash:
            continue   # CBS's adjacent double-render of the same row
        prev_cells_hash = row_hash
        ts_raw = _clean(cells[0])
        team_link = _TEAM_LINK_TXN_RE.search(cells[1])
        team_id = team_link.group(1) if team_link else None
        team_name = _clean(cells[1])
        fourth = _clean(cells[3])

        entries = []
        if _EFFECTIVE_RE.match(fourth):
            effective = fourth
            for pid, pname, segment in _txn_entries(cells[2]):
                text = _clean(segment)
                pos_span = _POS_TEAM_SPAN_RE.search(segment)
                if pid is not None:
                    action = (text.rsplit(' - ', 1)[1].strip()
                              if ' - ' in text else None)
                    entries.append((pid, pname,
                                    pos_span.group(1).strip() if pos_span else None,
                                    action))
                else:
                    # Packed era: 'Last, First POS TEAM - Added'
                    if ' - ' in text:
                        packed, action = text.rsplit(' - ', 1)
                    else:
                        packed, action = text, None
                    entries.append((None, packed.strip(), None,
                                    action.strip() if action else None))
        else:
            # 2001-era: player in cell 3, the action phrase IS cell 4.
            effective = None
            entries.append((None, _clean(cells[2]), None, fourth or None))

        rows.append({
            "txn_key": txn_key,
            "row_hash": row_hash,
            "ts_raw": ts_raw,
            "effective": effective,
            "team_id": team_id,
            "team_name": team_name,
            "entries": entries,
        })
    return rows


def _txn_page_sort_key(path):
    m = re.search(r'_r(\d+)\.html$', path.name)
    return int(m.group(1)) if m else 1


def walk_transactions(ui_root):
    # Prefer the seam-free corpus (MLB-119). transactions_v2/ holds one
    # whole-season file per year, fetched with ?print_rows=9999, so there are no
    # page boundaries. The old transactions/ archive was captured by walking
    # ?start_row, whose stride is derived from the rows WE parse and can drift
    # from CBS's own row numbering on unusually-grouped entries -- a four-line
    # commissioner trade in 2022 lost its "Traded from" line exactly that way,
    # which left two franchises both holding Lance Lynn for the whole season.
    #
    # The old archive is deliberately kept on disk (diffing, rollback) but is no
    # longer read when the v2 corpus exists. Do NOT merge the two: CBS assigns
    # row ids per query, so the same id denotes different content in the two
    # captures and any id-keyed reconciliation is meaningless.
    root = ui_root / "transactions_v2" / "all"
    if not root.is_dir():
        root = ui_root / "transactions" / "all"
        print("  transactions: v2 corpus absent -- falling back to the "
              "start_row archive (known lossy, see MLB-119)")
    unparsed = 0
    row_total = 0
    overlap_dropped = 0
    for year in sorted({int(re.match(r'(\d{4})', p.name).group(1))
                        for p in root.glob('*.html')}):
        row_seq = 0
        tail_hashes = []
        pages = sorted(root.glob(f'{year}*.html'), key=_txn_page_sort_key)
        for path in pages:
            rel = str(path.relative_to(ui_root)).replace("\\", "/")
            page_rows = parse_transactions_page(path, rel, year)
            # WINDOW-OVERLAP MERGE: an offset past the season's end makes
            # CBS clamp to the final window, which OVERLAPS the previous
            # page (shifted, so the capture's first-row repeat check
            # missed it). Consecutive windows can only overlap
            # prefix-onto-tail, so drop the page's leading rows that
            # match the accumulated tail, longest match first.
            hashes = [r["row_hash"] for r in page_rows]
            k = 0
            for cand in range(min(len(hashes), len(tail_hashes)), 0, -1):
                if hashes[:cand] == tail_hashes[-cand:]:
                    k = cand
                    break
            overlap_dropped += k
            page_rows = page_rows[k:]
            tail_hashes = (tail_hashes + [r["row_hash"] for r in page_rows])[-60:]
            for row in page_rows:
                row_seq += 1
                row_total += 1
                for entry_seq, (pid, pname, pos_team, action) in enumerate(
                        row["entries"], 1):
                    if action is None and pname:
                        unparsed += 1
                    yield "CBS_UI_TRANSACTIONS", {
                        "league_key": LEAGUE_KEY,
                        "season_year": year,
                        "txn_ts_raw": row["ts_raw"],
                        "effective_date_raw": row["effective"],
                        "team_id": row["team_id"],
                        "team_name": row["team_name"],
                        "player_cbs_id": pid,
                        "player_name_raw": pname,
                        "player_pos_team_raw": pos_team,
                        "action_raw": action,
                        "txn_row_key": row["txn_key"],
                        "row_seq": row_seq,
                        "entry_seq": entry_seq,
                        "source_path": rel,
                    }
    print(f"  transactions: {row_total} transaction rows walked "
          f"({overlap_dropped} window-overlap duplicates dropped); "
          f"{unparsed} entries lack an action phrase "
          f"(land with action_raw NULL -- inspect at staging)")


# ---------------------------------------------------------------------------
# drafts (MLB-90): ui/drafts/parsed/draft_rows.ndjson -> CBS_DRAFT.
#
# The odd one out. Every other family here parses captured HTML; the draft
# pages were already parsed by cbs_draft_parse.py, which deliberately
# stopped at NDJSON ("the evidence layer the RAW load will read when the
# modeling shape is signed off"). This walker is that load: it re-reads the
# parsed rows and lands them unchanged.
#
# Verbatim on purpose. The file holds BOTH the round and team views of each
# draft plus playerless order-only rows, and the per-season choice of which
# view to trust -- along with the is_playerless filter -- is a modeling
# decision that belongs in staging, not in the loader. Landing one view
# here would bake a judgement into RAW and throw away the evidence the
# other view provides.
#
# The rows already carry league_key and a repo-relative source_path, so
# idempotency works on the same key as every other family with no extra
# handling; only loaded_at is stamped by load_family().
# ---------------------------------------------------------------------------

DRAFT_NDJSON_REL = Path("drafts") / "parsed" / "draft_rows.ndjson"

# RAW mirrors the parsed record exactly. Named explicitly rather than
# splatting the dict so an upstream key addition surfaces here as a visible
# diff instead of silently vanishing at COPY (MATCH_BY_COLUMN_NAME drops
# unknown keys without complaint).
_DRAFT_FIELDS = (
    "league_key", "season_year", "draft_key", "draft_label", "period",
    "period_order", "view", "section_seq", "section_kind", "section_label",
    "row_seq", "page_seq", "pick_no", "round_num", "round_pick",
    "team_name_raw", "player_cbs_id", "player_name_raw", "pos_team_raw",
    "elig_raw", "salary_raw", "elapsed_raw", "rank_raw", "total_fpts",
    "active_fpts", "is_playerless", "parsed_at", "source_path",
)


def walk_drafts(ui_root):
    path = ui_root / DRAFT_NDJSON_REL
    if not path.is_file():
        print(f"  drafts: {path} not found -- run extract/cbs_draft_parse.py "
              f"first; skipping")
        return
    seen, unknown_keys = 0, set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parsed = json.loads(line)
            unknown_keys |= set(parsed) - set(_DRAFT_FIELDS)
            seen += 1
            yield "CBS_DRAFT", {k: parsed.get(k) for k in _DRAFT_FIELDS}
    if unknown_keys:
        print(f"  drafts: WARNING -- parsed rows carry keys RAW has no column "
              f"for: {sorted(unknown_keys)}. Add them to CBS_DRAFT + "
              f"_DRAFT_FIELDS or they are dropped silently.")
    print(f"  drafts: {seen} parsed pick rows walked (all views, playerless "
          f"rows included -- staging selects)")


FAMILIES = {"rosters": walk_rosters, "standings": walk_standings,
            "transactions": walk_transactions, "drafts": walk_drafts}


def build_config():
    cfg = {"account": os.getenv("SNOWFLAKE_ACCOUNT"), "user": os.getenv("SNOWFLAKE_USER"),
           "database": os.getenv("SNOWFLAKE_DATABASE"), "schema": os.getenv("SNOWFLAKE_SCHEMA"),
           "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE")}
    pk = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if pk:
        cfg["private_key_file"] = pk
        if os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            cfg["private_key_file_pwd"] = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    else:
        cfg["password"] = os.getenv("SNOWFLAKE_PASSWORD")
    return cfg


def loaded_paths(cur, table):
    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=CURRENT_SCHEMA() AND table_name=%s", (table,))
    if cur.fetchone()[0] == 0:
        return set()
    cur.execute(f"SELECT DISTINCT source_path FROM {table}")
    return {r[0] for r in cur.fetchall()}


def load_family(cur, family, walker, ui_root, scratch, force, dry_run):
    loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    counts, files_seen, skip_cache, writers, paths = {}, {}, {}, {}, {}
    try:
        for table, row in walker(ui_root):
            if table not in skip_cache:
                if not dry_run:
                    cur.execute(TABLES[table])
                skip_cache[table] = set() if force else loaded_paths(cur, table)
                if force and not dry_run:
                    cur.execute(f"TRUNCATE TABLE {table}")
            files_seen.setdefault(table, set()).add(row["source_path"])
            if row["source_path"] in skip_cache[table]:
                continue
            row["loaded_at"] = loaded_at
            if table not in writers:
                p = Path(scratch) / f"{table.lower()}.ndjson"
                paths[table] = p
                writers[table] = open(p, "w", encoding="utf-8")
            writers[table].write(json.dumps(row, ensure_ascii=False) + "\n")
            counts[table] = counts.get(table, 0) + 1
    finally:
        for w in writers.values():
            w.close()
    for table, n in counts.items():
        nf = len(files_seen.get(table, ()))
        if dry_run:
            print(f"  {table:<16} would load {n:>7} rows from {nf} files")
            continue
        sp = str(paths[table]).replace("\\", "/")
        cur.execute(f"PUT file://{sp} @%{table} OVERWRITE=TRUE AUTO_COMPRESS=TRUE")
        cur.execute(f"COPY INTO {table} FROM @%{table} "
                    "FILE_FORMAT=(TYPE=JSON) MATCH_BY_COLUMN_NAME=CASE_INSENSITIVE PURGE=TRUE")
        print(f"  {table:<16} loaded {n:>7} rows from {nf} files")
    return counts


def main():
    ap = argparse.ArgumentParser(description="Parse captured CBS UI history pages into RAW.")
    ap.add_argument("--data-dir", default=None,
                    help="path to data/cbs_raw (default <repo>/data/cbs_raw; the "
                         "raw archive usually lives in the MAIN checkout)")
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_dotenv()

    repo = Path(__file__).resolve().parents[1]
    data_root = Path(args.data_dir) if args.data_dir else repo / "data" / "cbs_raw"
    ui_root = data_root / "bsb" / "history" / "ui"
    if not ui_root.is_dir():
        raise SystemExit(f"{ui_root} not found -- pass --data-dir pointing at the "
                         f"checkout that holds data/cbs_raw.")
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"unknown families {unknown}; known {list(FAMILIES)}")

    print(f"parsing {ui_root}{' (DRY RUN)' if args.dry_run else ''}")
    with tempfile.TemporaryDirectory(prefix="cbs_ui_parse_") as scratch, \
            snowflake.connector.connect(**build_config()) as conn:
        cur = conn.cursor()
        try:
            for fam in families:
                print(f"\n{fam}:")
                load_family(cur, fam, FAMILIES[fam], ui_root, scratch,
                            args.force, args.dry_run)
            if not args.dry_run:
                conn.commit()
                print("\nVerification:")
                for table in TABLES:
                    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                                "WHERE table_schema=CURRENT_SCHEMA() AND table_name=%s",
                                (table,))
                    if cur.fetchone()[0] == 0:
                        print(f"  {table:<16} absent")
                        continue
                    cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT season_year), "
                                f"MIN(season_year), MAX(season_year) FROM {table}")
                    n, years, lo, hi = cur.fetchone()
                    print(f"  {table:<20} {n:>7} rows / {years} seasons / {lo}-{hi}")
                print("\nDone. Committed.")
            else:
                print("\nDry run complete.")
        finally:
            cur.close()


if __name__ == "__main__":
    main()
