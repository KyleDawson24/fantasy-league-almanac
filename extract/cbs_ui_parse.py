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


FAMILIES = {"rosters": walk_rosters, "standings": walk_standings}


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
                                f"COUNT(DISTINCT franchise_id), MIN(season_year), "
                                f"MAX(season_year) FROM {table}")
                    n, years, teams, lo, hi = cur.fetchone()
                    print(f"  {table:<16} {n:>7} rows / {years} seasons / "
                          f"{teams} franchise ids / {lo}-{hi}")
                print("\nDone. Committed.")
            else:
                print("\nDry run complete.")
        finally:
            cur.close()


if __name__ == "__main__":
    main()
