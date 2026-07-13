"""mlb_load.py -- universal MLB stats files -> warehouse RAW tables (MLB-70).

The disk->Snowflake half of the universal-stats path (extract/mlb_stats.py
lands files; dbt reads tables), mirroring extract/cbs_load.py for the CBS
archive. Payloads land VERBATIM in VARIANT columns; staging (MLB-62) does all
interpretation. The MLB Stats API is platform-neutral, so these tables carry
NO league_key -- they're the shared baseball layer that every league's
membership joins to.

Families -> tables:
  season/<mlbam>.json            -> MLB_SEASON_STATS  (row per player-season-group)
  gamelog/<mlbam>/<yr>_<grp>.json -> MLB_GAMELOGS      (row PER GAME -- the
                                     deliberate explosion; each game verbatim)
  fielding/<mlbam>.json          -> MLB_FIELDING      (row per player-season-
                                     POSITION: games/gamesStarted there -- the
                                     eligibility input, season grain)
  gamelog/<mlbam>/<yr>_<grp>.json -> MLB_GAME_POSITIONS (family "gamepos": the
                                     SAME files re-walked for the split-level
                                     positionsPlayed the gamelogs walker never
                                     projected -- one row per game-position,
                                     pitching games constant P. Dates the
                                     "Nth game at a position this season"
                                     eligibility achievement; no re-fetch)

Mechanics mirror cbs_load: rows stream to NDJSON, PUT to the table stage, COPY
INTO with MATCH_BY_COLUMN_NAME. Idempotent: files whose source_path is already
loaded are skipped. --force deletes + reloads the selection.

Usage:
  py extract/mlb_load.py                       # load everything new
  py extract/mlb_load.py --families gamelogs --dry-run
  py extract/mlb_load.py --force
"""

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

TABLES = {
    "MLB_SEASON_STATS": """CREATE TABLE IF NOT EXISTS MLB_SEASON_STATS (
        mlbam_id      INTEGER,
        season_year   INTEGER,
        stat_group    VARCHAR,
        stat          VARIANT,
        team          VARIANT,
        fetched_at    VARCHAR,
        source_path   VARCHAR,
        loaded_at     TIMESTAMP_NTZ
    )""",
    "MLB_GAMELOGS": """CREATE TABLE IF NOT EXISTS MLB_GAMELOGS (
        mlbam_id      INTEGER,
        season_year   INTEGER,
        stat_group    VARCHAR,
        game_date     DATE,
        game_index    INTEGER,
        game          VARIANT,
        fetched_at    VARCHAR,
        source_path   VARCHAR,
        loaded_at     TIMESTAMP_NTZ
    )""",
    "MLB_FIELDING": """CREATE TABLE IF NOT EXISTS MLB_FIELDING (
        mlbam_id      INTEGER,
        season_year   INTEGER,
        position      VARCHAR,
        stat          VARIANT,
        team          VARIANT,
        fetched_at    VARCHAR,
        source_path   VARCHAR,
        loaded_at     TIMESTAMP_NTZ
    )""",
    "MLB_GAME_POSITIONS": """CREATE TABLE IF NOT EXISTS MLB_GAME_POSITIONS (
        mlbam_id      INTEGER,
        season_year   INTEGER,
        stat_group    VARCHAR,
        game_date     DATE,
        game_pk       INTEGER,
        game_index    INTEGER,
        position      VARCHAR,
        fetched_at    VARCHAR,
        source_path   VARCHAR,
        loaded_at     TIMESTAMP_NTZ
    )""",
}


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


def read_doc(path, data_root):
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    rel = str(Path(path).relative_to(data_root)).replace("\\", "/")
    return doc, {"mlbam_id": doc.get("mlbam_id"), "fetched_at": doc.get("fetched_at"),
                 "source_path": rel}


def splits_of(doc):
    """statsapi payload -> list of split dicts (one per season or per game)."""
    stats = (doc.get("payload") or {}).get("stats") or []
    return stats[0].get("splits", []) if stats else []


def walk_season(root):
    for path in sorted((root / "season").glob("*.json")):
        doc, env = read_doc(path, root)
        stats = (doc.get("payload") or {}).get("stats") or []
        for block in stats:
            grp = (block.get("group") or {}).get("displayName")
            for sp in block.get("splits", []):
                yield "MLB_SEASON_STATS", {
                    **env, "season_year": _int(sp.get("season")), "stat_group": grp,
                    "stat": sp.get("stat"), "team": sp.get("team")}


def walk_gamelogs(root):
    for pdir in sorted((root / "gamelog").iterdir()):
        if not pdir.is_dir():
            continue
        for path in sorted(pdir.glob("*.json")):
            doc, env = read_doc(path, root)
            grp, season = doc.get("group"), _int(doc.get("season"))
            for i, sp in enumerate(splits_of(doc)):
                stat = sp.get("stat") or {}
                # each split is one game; carry the game's team + opponent verbatim
                game = {"stat": stat, "team": sp.get("team"), "opponent": sp.get("opponent"),
                        "isHome": sp.get("isHome"), "isWin": sp.get("isWin"),
                        "date": sp.get("date"), "game": sp.get("game")}
                yield "MLB_GAMELOGS", {
                    **env, "season_year": season, "stat_group": grp,
                    "game_date": sp.get("date"), "game_index": i, "game": game}


def walk_fielding(root):
    fdir = root / "fielding"
    if not fdir.is_dir():
        return
    for path in sorted(fdir.glob("*.json")):
        doc, env = read_doc(path, root)
        stats = (doc.get("payload") or {}).get("stats") or []
        for block in stats:
            for sp in block.get("splits", []):
                stat = sp.get("stat") or {}
                yield "MLB_FIELDING", {
                    **env, "season_year": _int(sp.get("season")),
                    "position": (stat.get("position") or {}).get("abbreviation"),
                    "stat": stat, "team": sp.get("team")}


def walk_gamepos(root):
    # Re-walks the gamelog files for the split-level positionsPlayed that
    # walk_gamelogs never projected into MLB_GAMELOGS.game. Hitting games
    # emit one row per position played; games with NO positionsPlayed
    # (pinch-hit-only appearances) emit nothing -- correct, since they
    # don't advance position-eligibility counters. Pitching games are
    # constant P (the discipline IS the position).
    for pdir in sorted((root / "gamelog").iterdir()):
        if not pdir.is_dir():
            continue
        for path in sorted(pdir.glob("*.json")):
            doc, env = read_doc(path, root)
            grp, season = doc.get("group"), _int(doc.get("season"))
            for i, sp in enumerate(splits_of(doc)):
                game_pk = (sp.get("game") or {}).get("gamePk")
                if grp == "pitching":
                    positions = ["P"]
                else:
                    positions = [(p or {}).get("abbreviation")
                                 for p in (sp.get("positionsPlayed") or [])]
                for pos in positions:
                    if not pos:
                        continue
                    yield "MLB_GAME_POSITIONS", {
                        **env, "season_year": season, "stat_group": grp,
                        "game_date": sp.get("date"), "game_pk": game_pk,
                        "game_index": i, "position": pos}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


FAMILIES = {"season": walk_season, "gamelogs": walk_gamelogs,
            "fielding": walk_fielding, "gamepos": walk_gamepos}


def loaded_paths(cursor, table):
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables "
                   "WHERE table_schema=CURRENT_SCHEMA() AND table_name=%s", (table,))
    if cursor.fetchone()[0] == 0:
        return set()
    cursor.execute(f"SELECT DISTINCT source_path FROM {table}")
    return {r[0] for r in cursor.fetchall()}


def load_family(cur, family, walker, root, scratch, force, dry_run):
    loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    counts, files_seen, skip_cache, writers, paths = {}, {}, {}, {}, {}
    try:
        for table, row in walker(root):
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
            print(f"  {table:<18} would load {n:>8} rows from {nf} files")
            continue
        sp = str(paths[table]).replace("\\", "/")
        cur.execute(f"PUT file://{sp} @%{table} OVERWRITE=TRUE AUTO_COMPRESS=TRUE")
        cur.execute(f"COPY INTO {table} FROM @%{table} "
                    "FILE_FORMAT=(TYPE=JSON) MATCH_BY_COLUMN_NAME=CASE_INSENSITIVE PURGE=TRUE")
        print(f"  {table:<18} loaded {n:>8} rows from {nf} files")
    return counts


def main():
    ap = argparse.ArgumentParser(description="Load universal MLB stats files into RAW.")
    ap.add_argument("--data-dir", default=None,
                    help="path to data/mlb_stats (default <repo>/data/mlb_stats)")
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_dotenv()

    repo = Path(__file__).resolve().parents[1]
    root = Path(args.data_dir) if args.data_dir else repo / "data" / "mlb_stats"
    if not root.is_dir():
        raise SystemExit(f"{root} not found -- run extract/mlb_stats.py first (pass --data-dir "
                         f"if the files live in the main checkout).")
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"unknown families {unknown}; known {list(FAMILIES)}")

    print(f"loading {root}{' (DRY RUN)' if args.dry_run else ''}")
    with tempfile.TemporaryDirectory(prefix="mlb_load_") as scratch, \
            snowflake.connector.connect(**build_config()) as conn:
        cur = conn.cursor()
        try:
            for fam in families:
                print(f"\n{fam}:")
                load_family(cur, fam, FAMILIES[fam], root, scratch, args.force, args.dry_run)
            if not args.dry_run:
                conn.commit()
                print("\nVerification:")
                for table in TABLES:
                    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                                "WHERE table_schema=CURRENT_SCHEMA() AND table_name=%s", (table,))
                    if cur.fetchone()[0] == 0:
                        print(f"  {table:<18} absent"); continue
                    cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT mlbam_id), MIN(season_year), "
                                f"MAX(season_year) FROM {table}")
                    n, players, lo, hi = cur.fetchone()
                    print(f"  {table:<18} {n:>8} rows / {players} players / {lo}-{hi}")
                print("\nDone. Committed.")
            else:
                print("\nDry run complete.")
        finally:
            cur.close()


if __name__ == "__main__":
    main()
