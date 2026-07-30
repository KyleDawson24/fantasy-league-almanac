"""Load the parquet RAW artifacts into the local DuckDB database (MLB-10
phase 1) -- the other half of dump_snowflake_raw_to_parquet.py.

The database file is named ESPN_FANTASY.duckdb on purpose: DuckDB takes
its catalog name from the file stem, so `source('raw', ...)` resolves to
ESPN_FANTASY.RAW.<table> on both engines and not one line of sources.yml
has to move for the port.

TYPES ARE PINNED FROM THE MANIFEST, NOT INFERRED FROM PARQUET. Two
reasons, both learned rather than assumed:

  * Snowflake's arrow encoder narrows integers to fit the values actually
    present -- SEASON_YEAR arrives as SMALLINT -- so the parquet schema is
    a function of the data in that dump and drifts as the league adds
    rows. Replaying the DECLARED Snowflake type gives a RAW schema that is
    identical every time.
  * DuckDB's FLOAT is 32 bits where Snowflake's is 64 (MLB-9 audit, the
    float32 trap: 553.7 landing as 553.5999755859375). Snowflake FLOAT
    therefore maps to DOUBLE here, never FLOAT. This is the landing-layer
    half of the same rule the models follow after MLB-134 spelled every
    cast `double`.

Usage (from the repo root):
    python tools/load_parquet_to_duckdb.py
    python tools/load_parquet_to_duckdb.py --db data/duckdb/ESPN_FANTASY.duckdb
"""

import argparse
import json
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET_DIR = REPO_ROOT / "data" / "parquet" / "raw"
DEFAULT_DB = REPO_ROOT / "data" / "duckdb" / "ESPN_FANTASY.duckdb"

# Snowflake declared type -> DuckDB type. NUMBER is resolved per column
# from its precision/scale (see duckdb_type).
SIMPLE_TYPES = {
    "TEXT": "VARCHAR",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP_NTZ": "TIMESTAMP",
    "TIMESTAMP_TZ": "TIMESTAMPTZ",
    "VARIANT": "JSON",
    # Snowflake FLOAT is 64-bit; DuckDB FLOAT is 32-bit. Never FLOAT.
    "FLOAT": "DOUBLE",
    "REAL": "DOUBLE",
    "DOUBLE": "DOUBLE",
}


def duckdb_type(column):
    """DuckDB type for one manifest column entry."""
    snowflake_type = column["snowflake_type"]
    if snowflake_type in SIMPLE_TYPES:
        return SIMPLE_TYPES[snowflake_type]
    if snowflake_type == "NUMBER":
        precision = column["precision"] or 38
        scale = column["scale"] or 0
        if scale == 0:
            # Every RAW NUMBER is NUMBER(38,0) -- ids, seasons, periods,
            # counts. BIGINT gives integer semantics matching Snowflake's
            # exact NUMBER and keeps downstream arithmetic (notably the
            # season_year*100+matchup_period watermark) in integer space.
            # The precision guard is here so a genuinely wide column added
            # later lands as DECIMAL rather than silently overflowing.
            return "BIGINT" if precision <= 18 else f"DECIMAL({precision},0)"
        return f"DECIMAL({precision},{scale})"
    raise ValueError(
        f"no DuckDB mapping for Snowflake type {snowflake_type!r} "
        f"(column {column['name']}); add one to SIMPLE_TYPES"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="path to the DuckDB database file")
    ap.add_argument("--parquet-dir", default=str(PARQUET_DIR))
    ap.add_argument("--memory-limit", default="6GB")
    args = ap.parse_args()

    parquet_dir = Path(args.parquet_dir)
    manifest_path = parquet_dir / "_manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no manifest at {manifest_path}; "
                 f"run tools/dump_snowflake_raw_to_parquet.py first")
    manifest = json.loads(manifest_path.read_text())

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = db_path.parent / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path))
    # Safety knobs before any work, matching the dbt profile. The MLB-9
    # spike's lesson was that these belong up front, not after trouble.
    con.execute(f"set memory_limit = '{args.memory_limit}'")
    con.execute(f"set temp_directory = '{temp_dir.as_posix()}'")
    con.execute("set max_temp_directory_size = '6GB'")
    con.execute("set preserve_insertion_order = false")
    con.execute("create schema if not exists RAW")

    print(f"Loading {len(manifest['tables'])} tables -> {db_path}")
    mismatches = []
    for entry in manifest["tables"]:
        table = entry["table"]
        parquet_path = parquet_dir / f"{table}.parquet"
        if not parquet_path.exists():
            sys.exit(f"manifest lists {table} but {parquet_path} is missing")

        projection = ",\n       ".join(
            f'cast("{c["name"]}" as {duckdb_type(c)}) as "{c["name"]}"'
            for c in entry["columns"]
        )
        con.execute(
            f'create or replace table RAW."{table}" as\n'
            f"select {projection}\n"
            f"from read_parquet('{parquet_path.as_posix()}')"
        )
        loaded = con.execute(f'select count(*) from RAW."{table}"').fetchone()[0]
        flag = ""
        if loaded != entry["rows"]:
            flag = f"  <== MISMATCH, manifest says {entry['rows']:,}"
            mismatches.append(table)
        print(f"  {table:<24} {loaded:>9,} rows{flag}")

    con.execute("checkpoint")
    size_mb = db_path.stat().st_size / 1e6
    total = con.execute(
        "select sum(estimated_size) from duckdb_tables() where schema_name = 'RAW'"
    ).fetchone()[0]
    con.close()

    print(f"\n{len(manifest['tables'])} tables, ~{int(total or 0):,} rows, "
          f"database {size_mb:.1f} MB")
    if mismatches:
        sys.exit(f"ROW COUNT MISMATCH in: {', '.join(mismatches)}")
    print("row counts match the dump manifest")


if __name__ == "__main__":
    main()
