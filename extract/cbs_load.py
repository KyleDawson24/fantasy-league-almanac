"""cbs_load.py -- CBS raw archives -> warehouse RAW tables (MLB-59).

The captures land files (extract/cbs_capture.py, extract/cbs_backfill.py);
dbt reads tables. This is the disk->Snowflake half of the CBS path,
mirroring what extract.py does for ESPN in one shot per archive family.

Naming follows the accepted MLB-48 appendix -- ONE RAW schema, platform-
prefixed tables (`raw.cbs_*`), every row carrying league_key -- which
supersedes the ticket's earlier per-league-schema sketch. Payloads land
VERBATIM per the museum/raw rule: envelope metadata becomes columns for
lineage (endpoint, params, captured_at, http_status, source file + sha);
`payload` (or the per-game entry) stays an untouched VARIANT. Staging
owns all interpretation (MLB-61).

Families -> tables:
  2026/rosters/<date>.json          -> CBS_ROSTERS      (row per date file)
  2026/standings/periods/period_N   -> CBS_STANDINGS    (row per period file)
  2026/transactions/snapshot_*.json -> CBS_TRANSACTIONS (row per snapshot)
  2026/config/<kind>_*.json         -> CBS_CONFIG       (row per snapshot; kind column)
  history/stats/<year>.json         -> CBS_SEASON_STATS (row per season file;
  history/stats_pitching/<year>.json   hitter + pitcher universes share the
                                       table -- params.position tells them apart)
  history/gamelog/<year>/<pid>.json -> CBS_GAMELOGS     (row PER GAME entry --
                                       the one deliberate explosion, per the
                                       ticket; each game object verbatim)

Mechanics: rows stream to NDJSON in a scratch dir, PUT to each table's
stage, COPY INTO with MATCH_BY_COLUMN_NAME. At gamelog volume (~237K
rows) INSERTs are not an option. Idempotent: files whose source_path is
already loaded are skipped (captures are append-only timestamped files
that never mutate); --force deletes and reloads the selection.
Verification: per-table rows-vs-files (or rows-vs-entries) printed at
the end; the loader asserts envelope http_status == 200 at read time
and refuses files that fail it.

Usage (data lives in the MAIN checkout; pass --data-dir when running
from a worktree):
  py extract/cbs_load.py --data-dir C:/Users/kyled/projects/espn-league-manager/data/cbs_raw
  py extract/cbs_load.py --families rosters,standings --dry-run
  py extract/cbs_load.py --force            # delete + reload everything selected
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.league_registry import LeagueRegistryError, get_league

# Envelope columns shared by every CBS raw table. `payload` is the
# verbatim response (or game entry); `params` the verbatim query params.
_COMMON_COLUMNS = """
    league_key      VARCHAR,
    league_slug     VARCHAR,
    season_year     INTEGER,
    endpoint        VARCHAR,
    params          VARIANT,
    captured_at     TIMESTAMP_TZ,
    http_status     INTEGER,
    source_path     VARCHAR,
    loaded_at       TIMESTAMP_NTZ
"""

TABLES = {
    "CBS_ROSTERS": f"""CREATE TABLE IF NOT EXISTS CBS_ROSTERS (
        roster_date     DATE,
        payload         VARIANT,
        {_COMMON_COLUMNS}
    )""",
    "CBS_STANDINGS": f"""CREATE TABLE IF NOT EXISTS CBS_STANDINGS (
        period          INTEGER,
        payload         VARIANT,
        {_COMMON_COLUMNS}
    )""",
    "CBS_TRANSACTIONS": f"""CREATE TABLE IF NOT EXISTS CBS_TRANSACTIONS (
        payload         VARIANT,
        {_COMMON_COLUMNS}
    )""",
    "CBS_CONFIG": f"""CREATE TABLE IF NOT EXISTS CBS_CONFIG (
        config_kind     VARCHAR,
        payload         VARIANT,
        {_COMMON_COLUMNS}
    )""",
    "CBS_SEASON_STATS": f"""CREATE TABLE IF NOT EXISTS CBS_SEASON_STATS (
        payload         VARIANT,
        {_COMMON_COLUMNS}
    )""",
    "CBS_GAMELOGS": f"""CREATE TABLE IF NOT EXISTS CBS_GAMELOGS (
        player_id       VARCHAR,
        game_index      INTEGER,
        game            VARIANT,
        {_COMMON_COLUMNS}
    )""",
}


def build_config():
    """Snowflake connection to the RAW landing schema -- same env
    contract as extract.py / tools/migrate_raw_league_key.py."""
    config = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    }
    private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if private_key_path:
        config["private_key_file"] = private_key_path
        passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        if passphrase:
            config["private_key_file_pwd"] = passphrase
    else:
        config["password"] = os.getenv("SNOWFLAKE_PASSWORD")
    return config


def read_envelope(path, data_root):
    """Load one capture file and return (envelope_columns, payload).
    Refuses non-200 captures -- those should never have been written,
    and quietly loading one would poison staging's trust in RAW."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    status = doc.get("http_status")
    if status != 200:
        raise ValueError(f"{path}: http_status={status!r}, refusing to load")
    rel = str(Path(path).relative_to(data_root)).replace("\\", "/")
    envelope = {
        "league_slug": doc.get("league"),
        "endpoint": doc.get("endpoint"),
        "params": doc.get("params"),
        "captured_at": doc.get("captured_at"),
        "http_status": status,
        "source_path": rel,
    }
    return doc, envelope


def season_from(doc):
    """The season the DATA describes: params.timeframe for history
    pulls (the endpoint's history key), else the envelope's season."""
    params = doc.get("params") or {}
    tf = params.get("timeframe")
    if tf is not None:
        try:
            return int(tf)
        except (TypeError, ValueError):
            pass
    return doc.get("season")


# ---------------------------------------------------------------------------
# Family walkers: yield (table, row_dict) per output row.
# ---------------------------------------------------------------------------

def walk_rosters(root):
    for path in sorted((root / "2026" / "rosters").glob("*.json")):
        doc, env = read_envelope(path, root)
        yield "CBS_ROSTERS", {
            **env,
            "season_year": doc.get("season"),
            "roster_date": path.stem,  # 2026-03-25
            "payload": doc.get("payload"),
        }


def walk_standings(root):
    for path in sorted((root / "2026" / "standings" / "periods").glob("period_*.json")):
        doc, env = read_envelope(path, root)
        period = int(path.stem.split("_")[1])
        yield "CBS_STANDINGS", {
            **env,
            "season_year": doc.get("season"),
            "period": period,
            "payload": doc.get("payload"),
        }


def walk_transactions(root):
    for path in sorted((root / "2026" / "transactions").glob("snapshot_*.json")):
        doc, env = read_envelope(path, root)
        yield "CBS_TRANSACTIONS", {
            **env,
            "season_year": doc.get("season"),
            "payload": doc.get("payload"),
        }


def walk_config(root):
    for path in sorted((root / "2026" / "config").glob("*.json")):
        doc, env = read_envelope(path, root)
        # details_2026-07-08T012451Z.json -> kind 'details';
        # league_rules_... -> 'league_rules' (kind is everything before
        # the _<timestamp> suffix).
        kind = "_".join(path.stem.split("_")[:-1])
        yield "CBS_CONFIG", {
            **env,
            "season_year": doc.get("season"),
            "config_kind": kind,
            "payload": doc.get("payload"),
        }


def walk_season_stats(root):
    # Hitter universes (stats/) and pitcher universes (stats_pitching/,
    # from league/stats?position=P -- the MLB-45 pitching sweep) share
    # one table; the envelope's params.position distinguishes them, so
    # the payloads stay verbatim and staging does the telling-apart.
    for sub in ("stats", "stats_pitching"):
        for path in sorted((root / "history" / sub).glob("*.json")):
            doc, env = read_envelope(path, root)
            yield "CBS_SEASON_STATS", {
                **env,
                "season_year": season_from(doc),
                "payload": doc.get("payload"),
            }


def walk_gamelogs(root):
    """The one deliberate explosion: one row per game entry, each entry
    verbatim (postponed-game placeholder rows included -- staging decides
    what a null game_date means, not the loader).

    33 player-seasons return a valid 200 with an EMPTY gamelog (keyhole-
    era captures where the season entry exists but no schedule rows do).
    Those land one sentinel row with game IS NULL so every captured file
    is represented in RAW -- file-level lineage and idempotent-skip both
    depend on the path being present. Staging filters game IS NOT NULL."""
    for year_dir in sorted((root / "history" / "gamelog").iterdir()):
        if not year_dir.is_dir():
            continue
        for path in sorted(year_dir.glob("*.json")):
            doc, env = read_envelope(path, root)
            games = (doc.get("payload") or {}).get("body", {}).get("gamelog") or []
            player_id = (doc.get("params") or {}).get("player_id") or path.stem
            season = season_from(doc)
            if not games:
                yield "CBS_GAMELOGS", {
                    **env,
                    "season_year": season,
                    "player_id": str(player_id),
                    "game_index": None,
                    "game": None,
                }
                continue
            for i, game in enumerate(games):
                yield "CBS_GAMELOGS", {
                    **env,
                    "season_year": season,
                    "player_id": str(player_id),
                    "game_index": i,
                    "game": game,
                }


FAMILIES = {
    "rosters": walk_rosters,
    "standings": walk_standings,
    "transactions": walk_transactions,
    "config": walk_config,
    "season_stats": walk_season_stats,
    "gamelogs": walk_gamelogs,
}


# ---------------------------------------------------------------------------
# Load mechanics
# ---------------------------------------------------------------------------

def loaded_paths(cursor, table):
    """source_paths already in the table (captures are append-only
    timestamped files, so path presence == loaded)."""
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = CURRENT_SCHEMA() AND table_name = %s", (table,))
    if cursor.fetchone()[0] == 0:
        return set()
    cursor.execute(f"SELECT DISTINCT source_path FROM {table}")
    return {r[0] for r in cursor.fetchall()}


def load_family(cursor, family, walker, root, league_key, scratch,
                force=False, dry_run=False):
    loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows_by_table = {}
    files_seen = {}
    skip_cache = {}

    ndjson_paths = {}
    writers = {}
    try:
        for table, row in walker(root):
            if table not in skip_cache:
                if not dry_run:
                    cursor.execute(TABLES[table])
                # loaded_paths handles an absent table (dry runs must not
                # create anything, but should still report real skips).
                skip_cache[table] = set() if force else loaded_paths(cursor, table)
                if force and not dry_run:
                    # Scoped wipe: only this family's table, only this
                    # league's rows.
                    cursor.execute(f"DELETE FROM {table} WHERE league_key = %s",
                                   (league_key,))
            files_seen.setdefault(table, set()).add(row["source_path"])
            if row["source_path"] in skip_cache[table]:
                continue
            row["league_key"] = league_key
            row["loaded_at"] = loaded_at
            if table not in writers:
                p = Path(scratch) / f"{table.lower()}.ndjson"
                ndjson_paths[table] = p
                writers[table] = open(p, "w", encoding="utf-8")
            writers[table].write(json.dumps(row, ensure_ascii=False) + "\n")
            rows_by_table[table] = rows_by_table.get(table, 0) + 1
    finally:
        for w in writers.values():
            w.close()

    for table, n in rows_by_table.items():
        n_files = len(files_seen.get(table, ()))
        if dry_run:
            print(f"  {table:<18} would load {n:>7} rows from {n_files} files")
            continue
        stage_path = str(ndjson_paths[table]).replace("\\", "/")
        cursor.execute(
            f"PUT file://{stage_path} @%{table} OVERWRITE=TRUE AUTO_COMPRESS=TRUE"
        )
        cursor.execute(f"""
            COPY INTO {table}
            FROM @%{table}
            FILE_FORMAT = (TYPE = JSON)
            MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
            PURGE = TRUE
        """)
        print(f"  {table:<18} loaded {n:>7} rows from {n_files} files")

    skipped = {
        t: len(files_seen.get(t, ())) - len({
            p for p in files_seen.get(t, ()) if p not in skip_cache.get(t, ())
        })
        for t in files_seen
    }
    for t, s in skipped.items():
        if s:
            print(f"  {t:<18} skipped {s} already-loaded files")
    return rows_by_table


def verify(cursor, league_key):
    """Post-load verification: warehouse row counts per table."""
    print("\nVerification (warehouse counts for this league):")
    for table in TABLES:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = CURRENT_SCHEMA() AND table_name = %s", (table,))
        if cursor.fetchone()[0] == 0:
            print(f"  {table:<18} absent")
            continue
        cursor.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT source_path), MIN(season_year), "
            f"MAX(season_year) FROM {table} WHERE league_key = %s", (league_key,))
        rows, files, lo, hi = cursor.fetchone()
        print(f"  {table:<18} {rows:>7} rows / {files:>5} files / seasons {lo}-{hi}")


def main():
    parser = argparse.ArgumentParser(
        description="Load the CBS raw archives into warehouse RAW tables.")
    parser.add_argument(
        "--data-dir", default=None,
        help="Path to data/cbs_raw (default: <repo>/data/cbs_raw). The "
             "archive lives in the MAIN checkout; pass this when running "
             "from a worktree.")
    parser.add_argument(
        "--league", default="cbs-bsb", metavar="LEAGUE_KEY",
        help="League registry key to stamp (must be a cbs-platform league).")
    parser.add_argument(
        "--families", default=",".join(FAMILIES),
        help=f"Comma list of families to load (default: all). "
             f"Known: {', '.join(FAMILIES)}")
    parser.add_argument("--force", action="store_true",
                        help="Delete this league's rows in the selected "
                             "families' tables and reload from disk.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Walk + count everything, load nothing.")
    args = parser.parse_args()

    load_dotenv()
    try:
        league = get_league(args.league)
        if league.platform != "cbs":
            raise LeagueRegistryError(
                f"cbs_load.py loads cbs-platform archives; league "
                f"'{league.key}' is platform '{league.platform}'.")
    except LeagueRegistryError as e:
        raise SystemExit(f"[league registry] {e}")

    repo_root = Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir) if args.data_dir else repo_root / "data" / "cbs_raw"
    slug = league.resolve_league_id()
    root = data_dir / slug
    if not root.is_dir():
        raise SystemExit(
            f"Archive root {root} not found. The captures are gitignored and "
            f"live in the main checkout -- pass --data-dir.")

    families = [f.strip() for f in args.families.split(",") if f.strip()]
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"Unknown families: {unknown}. Known: {list(FAMILIES)}")

    print(f"Loading {root} as league_key={league.key!r}"
          f"{' (DRY RUN)' if args.dry_run else ''}")

    with tempfile.TemporaryDirectory(prefix="cbs_load_") as scratch, \
            snowflake.connector.connect(**build_config()) as conn:
        cursor = conn.cursor()
        try:
            for family in families:
                print(f"\n{family}:")
                load_family(cursor, family, FAMILIES[family], root,
                            league.key, scratch,
                            force=args.force, dry_run=args.dry_run)
            if not args.dry_run:
                conn.commit()
                verify(cursor, league.key)
                print("\nDone. Committed.")
            else:
                print("\nDry run complete. Nothing loaded.")
        finally:
            cursor.close()


if __name__ == "__main__":
    main()
