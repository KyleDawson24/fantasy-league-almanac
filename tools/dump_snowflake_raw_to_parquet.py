"""Dump Snowflake RAW to parquet -- the landing artifacts the DuckDB target
reads (MLB-10 phase 1).

WHY PARQUET AND NOT A DIRECT SNOWFLAKE->DUCKDB COPY: the parquet files are
the single source of truth for RAW that BOTH engines can load, which is
what makes the port a dual-target arrangement rather than a migration
(MLB-10's architecture note). It is also the vendor-risk hedge -- the
league's whole history stops living exclusively inside one account -- and
it is what lets MLB-11's recruiter demo ship a sample league as files.

VARIANT HANDLING is the one non-obvious part. A VARIANT column is pulled
through TO_JSON() rather than selected bare: bare selection can hand back
Snowflake's pretty-printed form (newlines and indentation), which is
semantically identical JSON but byte-unstable across runs, and this dump
is a comparison substrate. TO_JSON gives compact, deterministic text.
The column lands in parquet as a plain string and the loader casts it
back to DuckDB's JSON type -- see load_parquet_to_duckdb.py, which reads
the variant-column list out of the manifest this script writes.

Column names stay UPPERCASE to mirror Snowflake's stored identifier case,
so staging models resolve identically against either engine.

Read-only against Snowflake: SELECTs and information_schema, nothing else.

Usage (from the repo root):
    python tools/dump_snowflake_raw_to_parquet.py
    python tools/dump_snowflake_raw_to_parquet.py --force        # re-dump all
    python tools/dump_snowflake_raw_to_parquet.py --table BOX_SCORES

--schema ANALYTICS dumps the dbt-built models instead (views are
materialized by the SELECT), into data/parquet/analytics/. That is how a
scratch DuckDB gets the warehouse's exact modeled state without running
dbt: the dashboard extract (MLB-319) reads models, and a model rebuilt
on another engine is a second answer, not the same one. --exclude skips
relations nothing downstream reads (STG_MLB__PLAYER_GAME is 42M rows).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import snowflake.connector
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "parquet" / "raw"
MANIFEST = OUT_DIR / "_manifest.json"

# JSON-shaped Snowflake types, pulled through TO_JSON (module docstring).
SEMI_STRUCTURED = ("VARIANT", "ARRAY", "OBJECT")

# Batch size for the streaming fetch. MLB_GAMELOGS is 1.8M rows of nested
# JSON; materializing that as one arrow table is the kind of thing that
# ballooned the pagefile during the MLB-9 spike, so the dump streams
# batches straight into the parquet writer and never holds the whole
# table in memory.
FETCH_BATCH_ROWS = 50_000


def build_config(schema="RAW"):
    """Snowflake connector config from .env -- same key-pair-preferred
    resolution as output/db.py, kept independent so this script has no
    import dependency on the output layer."""
    load_dotenv(REPO_ROOT / ".env")
    cfg = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "database": os.getenv("SNOWFLAKE_DATABASE", "ESPN_FANTASY"),
        "schema": schema,
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    }
    key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        cfg["private_key_file"] = key_path
        passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        if passphrase:
            cfg["private_key_file_pwd"] = passphrase
    else:
        cfg["password"] = os.getenv("SNOWFLAKE_PASSWORD")
    return cfg


def describe_raw(cur, schema="RAW"):
    """{table_name: [(column_name, data_type, precision, scale), ...]} for
    RAW, in ordinal order.

    Precision/scale ride along because the loader pins DuckDB types from
    them rather than trusting parquet inference. Snowflake's arrow encoder
    picks the narrowest physical int that fits the values actually present
    -- SEASON_YEAR came back SMALLINT -- so the parquet schema is a
    function of the data, not of the source schema, and would drift
    between dumps. The declared Snowflake type is the stable thing, so
    that is what gets recorded and replayed."""
    cur.execute(
        """
        select table_name, column_name, data_type,
               numeric_precision, numeric_scale
        from ESPN_FANTASY.information_schema.columns
        where table_schema = %s
        order by table_name, ordinal_position
        """,
        (schema,),
    )
    tables = {}
    for table_name, column_name, data_type, precision, scale in cur.fetchall():
        tables.setdefault(table_name, []).append(
            (column_name, data_type, precision, scale)
        )
    return tables


def select_list(columns):
    """Build the projection. VARIANT -> TO_JSON(col) AS col (see module
    docstring); everything else passes through under its own name. Every
    identifier is quoted because RAW carries at least one column that is a
    reserved word downstream (CBS_DRAFT."VIEW")."""
    parts = []
    for name, dtype, _precision, _scale in columns:
        if dtype in SEMI_STRUCTURED:
            parts.append(f'to_json("{name}") as "{name}"')
        else:
            parts.append(f'"{name}"')
    return ",\n       ".join(parts)


def manifest_entry(table, rows, out_path, columns):
    return {
        "table": table,
        "rows": rows,
        "bytes": out_path.stat().st_size,
        "variant_columns": [name for name, dtype, _p, _s in columns
                            if dtype in SEMI_STRUCTURED],
        "columns": [
            {"name": name, "snowflake_type": dtype,
             "precision": precision, "scale": scale}
            for name, dtype, precision, scale in columns
        ],
    }


def dump_table(cur, table, columns, force, schema="RAW", where=None):
    out_path = OUT_DIR / f"{table}.parquet"
    if out_path.exists() and not force:
        # A skipped file still belongs in the manifest: a resumed run
        # (after one relation failed) must describe every file on disk,
        # or the loader silently omits the ones the first attempt wrote.
        print(f"  {table:<24} SKIP (exists; --force to re-dump)")
        entry = manifest_entry(table, pq.ParquetFile(out_path).metadata.num_rows,
                               out_path, columns)
        if where:
            entry["where"] = where
        return entry

    sql = f'select {select_list(columns)}\nfrom ESPN_FANTASY.{schema}."{table}"'
    if where:
        # A row filter makes this file a SUBSET; the manifest records it.
        sql += f"\nwhere {where}"

    started = time.time()
    cur.execute(sql)

    # Write to a temp path and move on success, so an interrupted dump can
    # never leave a half-written parquet that the next run would "SKIP".
    tmp_path = out_path.with_suffix(".parquet.partial")
    writer = None
    rows = 0
    try:
        # fetch_arrow_batches yields pyarrow Tables (not RecordBatches) on
        # snowflake-connector 4.x. Normalize so the writer takes either.
        for batch in cur.fetch_arrow_batches():
            if not isinstance(batch, pa.Table):
                batch = pa.Table.from_batches([batch])
            if writer is None:
                writer = pq.ParquetWriter(tmp_path, batch.schema, compression="zstd")
            elif batch.schema != writer.schema:
                # Snowflake can widen a nullable column's arrow type between
                # batches (an all-NULL first chunk types as null). Conform to
                # the schema the file was opened with rather than failing.
                batch = batch.cast(writer.schema)
            writer.write_table(batch)
            rows += batch.num_rows
        if writer is None:
            # Empty table: still emit a file so the loader's table list is
            # complete and the DuckDB build sees the same 22 relations.
            schema = pa.schema([(name, pa.string()) for name, _d, _p, _s in columns])
            writer = pq.ParquetWriter(tmp_path, schema, compression="zstd")
    finally:
        if writer is not None:
            writer.close()

    if out_path.exists():
        out_path.unlink()
    tmp_path.rename(out_path)

    size_mb = out_path.stat().st_size / 1e6
    elapsed = time.time() - started
    print(f"  {table:<24} {rows:>9,} rows  {size_mb:>7.1f} MB  {elapsed:>6.1f}s")
    entry = manifest_entry(table, rows, out_path, columns)
    if where:
        entry["where"] = where
    return entry


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="re-dump tables whose parquet already exists")
    ap.add_argument("--table", action="append", dest="tables",
                    help="dump only this table (repeatable)")
    ap.add_argument("--schema", default="RAW",
                    help="source schema (RAW or ANALYTICS)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="skip this relation (repeatable)")
    ap.add_argument("--where", action="append", default=[], metavar="TABLE=PREDICATE",
                    help="dump only the rows of TABLE matching PREDICATE (repeatable)")
    ap.add_argument("--keep-going", action="store_true",
                    help="record relations that fail to select and continue")
    args = ap.parse_args()

    global OUT_DIR, MANIFEST
    schema = args.schema.upper()
    if schema != "RAW":
        OUT_DIR = REPO_ROOT / "data" / "parquet" / schema.lower()
        MANIFEST = OUT_DIR / "_manifest.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = snowflake.connector.connect(**build_config(schema))
    cur = conn.cursor()
    try:
        all_tables = describe_raw(cur, schema)
        wanted = args.tables or sorted(all_tables)
        unknown = [t for t in wanted if t not in all_tables]
        if unknown:
            sys.exit(f"unknown {schema} relation(s): {', '.join(unknown)}")
        excluded = {t.upper() for t in args.exclude}
        wheres = {t.upper(): pred for t, pred in (w.split("=", 1) for w in args.where)}
        wanted = [t for t in wanted if t not in excluded]

        print(f"Dumping {len(wanted)} {schema} relation(s) -> {OUT_DIR}")
        entries = {}
        if MANIFEST.exists():
            entries = {e["table"]: e for e in json.loads(MANIFEST.read_text())["tables"]}

        broken = {}
        for table in wanted:
            try:
                result = dump_table(cur, table, all_tables[table], args.force, schema,
                                    wheres.get(table))
            except snowflake.connector.errors.ProgrammingError as exc:
                # A view over a dropped model fails to expand. With
                # --keep-going it is recorded and skipped rather than
                # aborting a dump that is otherwise sound.
                if not args.keep_going:
                    raise
                broken[table] = str(exc).splitlines()[-1]
                print(f"  {table:<24} BROKEN: {broken[table]}")
                continue
            if result is not None:
                entries[table] = result

        MANIFEST.write_text(json.dumps(
            {"source": f"ESPN_FANTASY.{schema}",
             "tables": [entries[t] for t in sorted(entries)],
             "broken": broken},
            indent=2,
        ))
        total_rows = sum(e["rows"] for e in entries.values())
        total_mb = sum(e["bytes"] for e in entries.values()) / 1e6
        print(f"\n{len(entries)} tables, {total_rows:,} rows, {total_mb:.1f} MB")
        print(f"manifest -> {MANIFEST}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
