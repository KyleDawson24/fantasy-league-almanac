"""Where RAW lands: the local parquet sink (MLB-208).

WHAT THIS IS. `extract.py` has always had exactly one place to put RAW --
Snowflake -- so "extract the league" and "have a warehouse account" were the
same requirement. This module adds a second producer of an ALREADY-CONSUMED
contract: the parquet artifacts + `_manifest.json` that
`tools/load_parquet_to_duckdb.py` reads today, unchanged, and that the
byte-parity DuckDB port already ran on. The socket exists and works; this is
a second plug, not a new appliance.

WHY THE TWO IMPLEMENTATIONS LIVE IN DIFFERENT FILES. `SnowflakeSink` is a
thin adapter in extract.py over the `load_*_to_snowflake` functions, left
exactly where they are. Relocating them for symmetry would move the
delete-then-insert that MLB-188 calls "the irreversible one" -- along with
the comment block explaining why -- for no behavioural gain, on the path
Kyle's weekly run depends on. Settled-RAW protection outranks tidiness. So
the NEW code is here and the PROVEN code did not move.

WHAT THIS MODULE MUST NOT DO, and it is a hard constraint rather than a
preference: no module-scope environment reads, and no Snowflake import at
module scope. `extract.py` raises on import without LEAGUE_ID set, so a
fresh clone cannot import it at all -- which is precisely the machine the
pure conformance test has to run on. This module is importable with no
.env, no credentials and no network.

TYPES COME FROM THE CONTRACT, NEVER FROM THE DATA. config/raw_schema_contract.json
is generated from Snowflake's information_schema and committed as the
engine-neutral authority. Inferring a schema from whatever rows this run
happened to produce would drift exactly the way the loader's docstring
warns about (Snowflake's arrow encoder narrowing SEASON_YEAR to SMALLINT).
An empty first extract and a full backfill must produce the same schema.

APPEND VS OVERWRITE mirrors each table's existing warehouse DML, because the
staging models are written against those semantics:

  BOX_SCORES        delete-then-insert scoped to (season_year, matchup_period,
                    league_key) -- so re-running a period replaces it
  the other five    APPEND-ONLY snapshots. stg_* picks the latest per
                    (league_key, season_year) via ROW_NUMBER() ORDER BY
                    extracted_at DESC. Collapsing these to overwrite still
                    runs, still returns one row, and silently discards the
                    snapshot history the ELT design keeps on purpose.

Writes go to `<TABLE>.parquet.partial` and are renamed over the target, the
same way dump_snowflake_raw_to_parquet.py does it, so an interrupted run
cannot leave a half-written file that the next run treats as good.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = REPO_ROOT / "config" / "raw_schema_contract.json"
DEFAULT_PARQUET_DIR = REPO_ROOT / "data" / "parquet" / "raw"

# The six RAW tables the ESPN extract produces. CBS (extract/cbs_load.py) and
# the MLB Stats spine (extract/mlb_load.py) also land in RAW but are out of
# MLB-208's scope -- the ticket is ESPN-only, CBS BYO is gated on the D-05
# registry threading.
ESPN_RAW_TABLES = (
    "BOX_SCORES",
    "SCORING_SETTINGS",
    "ROSTER_SETTINGS",
    "TEAM_OWNERS",
    "DRAFT_PICKS",
    "TRANSACTIONS",
)

# Contract type -> pyarrow type. Deliberately NOT the same mapping as the
# loader's Snowflake->DuckDB one: this is the on-disk physical type, and the
# loader casts from it to the pinned DuckDB type afterwards. The two agree
# where it matters (a VARIANT is JSON text on disk in both paths, because
# the Snowflake dump pulls it through TO_JSON()).
#
# NUMBER(scale 0) lands as int64, and that is the PROVEN shape rather than
# the pedantic one. The physical parquet type does not decide the DuckDB
# type -- load_parquet_to_duckdb.py pins that from the contract and CASTS on
# the way in (RAW NUMBER(38,0) becomes DECIMAL(38,0); verified against the
# deployed database). So on-disk only has to cast losslessly, and the
# Snowflake dump has been feeding that cast even NARROWER integers all along
# (its arrow encoder returns SMALLINT for SEASON_YEAR). int64 is therefore
# the same kind of input the byte-parity port already ran on, where
# decimal128 would be a new one.
#
# Fails loudly, not silently, if that assumption ever breaks: RAW's NUMBER
# columns are ids, seasons, periods and counts, and a value that genuinely
# needed more than 63 bits would raise on array construction rather than
# wrap.
_ARROW_TYPES = {
    "TEXT": pa.string(),
    "BOOLEAN": pa.bool_(),
    "DATE": pa.date32(),
    "TIMESTAMP_NTZ": pa.timestamp("us"),
    "TIMESTAMP_TZ": pa.timestamp("us", tz="UTC"),
    # A VARIANT is stored as compact JSON TEXT on disk; the loader casts it
    # to DuckDB's JSON type. Same shape the Snowflake dump produces.
    "VARIANT": pa.string(),
    # Snowflake FLOAT is 64-bit. Never float32 -- the 553.7 ->
    # 553.5999755859375 trap from the MLB-9 audit.
    "FLOAT": pa.float64(),
    "REAL": pa.float64(),
    "DOUBLE": pa.float64(),
}


class RawSchemaError(RuntimeError):
    """The contract could not be applied to this table."""


def load_contract(contract_path=None):
    path = Path(contract_path or DEFAULT_CONTRACT_PATH)
    if not path.exists():
        raise RawSchemaError(
            f"no RAW type contract at {path}. It is generated from Snowflake's "
            f"information_schema by tools/generate_raw_schema_contract.py and "
            f"committed; a checkout without it cannot write typed RAW."
        )
    return json.loads(path.read_text())


def arrow_type(column):
    """pyarrow type for one contract column entry."""
    declared = column["snowflake_type"]
    if declared in _ARROW_TYPES:
        return _ARROW_TYPES[declared]
    if declared == "NUMBER":
        scale = column["scale"] or 0
        precision = column["precision"] or 38
        if scale == 0:
            return pa.int64()
        return pa.decimal128(precision, scale)
    raise RawSchemaError(
        f"no parquet mapping for contract type {declared!r} "
        f"(column {column['name']}); add one to _ARROW_TYPES"
    )


def arrow_schema(columns):
    """Full pyarrow schema for a contract table, in the contract's order.

    Order matters and is the catalog's ordinal order, not the CREATE TABLE
    literal's: BOX_SCORES' DDL lists season_year first but its deployed
    ordinal 1 is SCORING_PERIOD, because the table evolved after creation.
    """
    return pa.schema([(c["name"], arrow_type(c)) for c in columns])


def to_json_text(payload):
    """Compact, deterministic JSON for a VARIANT column.

    Compact because the Snowflake dump pulls VARIANT through TO_JSON() for
    exactly this reason -- bare selection can hand back a pretty-printed form
    that is semantically identical and byte-unstable, and these files are a
    comparison substrate. Keys are NOT sorted: payloads land verbatim in the
    order the platform sent them.
    """
    return json.dumps(payload, separators=(",", ":"))


class LocalParquetSink:
    """Writes RAW as parquet + a manifest, with no warehouse anywhere.

    Emits precisely the artifacts `tools/load_parquet_to_duckdb.py` already
    consumes, so the contract is proven by the existing consumer rather than
    by a second one written alongside it.

    `parquet_dir` is a constructor argument and no method reads a module-level
    path: pointing this at fixture data for the packaged demo (MLB-11) is a
    call-site change, not a code change. There is deliberately no
    fixture-specific branch in here.
    """

    name = "local parquet"

    def __init__(self, parquet_dir=None, contract_path=None, quiet=False):
        self.parquet_dir = Path(parquet_dir or DEFAULT_PARQUET_DIR)
        self.contract = load_contract(contract_path)
        self.quiet = quiet

    # -- description -----------------------------------------------------
    def describe(self):
        return f"{self.name} -> {self.parquet_dir}"

    def _log(self, message):
        if not self.quiet:
            print(message)

    # -- contract plumbing ------------------------------------------------
    def columns(self, table):
        try:
            return self.contract["tables"][table]
        except KeyError:
            raise RawSchemaError(
                f"{table} is not in the RAW type contract "
                f"({', '.join(sorted(self.contract['tables']))}). Regenerate "
                f"with tools/generate_raw_schema_contract.py."
            ) from None

    def schema(self, table):
        return arrow_schema(self.columns(table))

    def path_for(self, table):
        return self.parquet_dir / f"{table}.parquet"

    # -- read / write primitives -----------------------------------------
    def _read_existing(self, table):
        """Existing rows conformed to the contract schema, or None.

        Conformed rather than trusted: a file written by the Snowflake dump
        carries whatever arrow types Snowflake's encoder chose for the values
        in THAT dump (SEASON_YEAR came back SMALLINT), so concatenating it
        with contract-shaped new rows would fail on a schema mismatch. Casting
        to the contract is the same "replay the declared type" rule the loader
        follows, applied one step earlier.
        """
        path = self.path_for(table)
        if not path.exists():
            return None
        existing = pq.read_table(path)
        target = self.schema(table)
        if existing.schema == target:
            return existing
        try:
            return existing.select(target.names).cast(target)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, KeyError) as exc:
            raise RawSchemaError(
                f"{path} does not conform to the RAW contract for {table} "
                f"and could not be cast to it ({exc}). Refusing to write: "
                f"appending to a file whose shape is unknown risks the rows "
                f"already in it."
            ) from exc

    def _write(self, table, arrow_table):
        """Atomic replace: write .partial, then rename over the target."""
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        final = self.path_for(table)
        partial = final.with_suffix(".parquet.partial")
        pq.write_table(arrow_table, partial, compression="zstd")
        if final.exists():
            final.unlink()
        partial.rename(final)
        return final

    def _rows_to_arrow(self, table, rows):
        schema = self.schema(table)
        columns = {name: [] for name in schema.names}
        for row in rows:
            for name in schema.names:
                columns[name].append(row.get(name))
        return pa.table([pa.array(columns[n], type=schema.field(n).type)
                         for n in schema.names], schema=schema)

    def _append(self, table, rows, keep_mask=None, label=None):
        """Read-modify-write one table.

        `keep_mask` is a callable taking the existing arrow table and
        returning a boolean mask of rows to KEEP -- the local expression of
        Snowflake's scoped DELETE. None means append-only.
        """
        existing = self._read_existing(table)
        dropped = 0
        if existing is not None and keep_mask is not None:
            mask = keep_mask(existing)
            before = existing.num_rows
            existing = existing.filter(mask)
            dropped = before - existing.num_rows

        new_rows = self._rows_to_arrow(table, rows)
        combined = (pa.concat_tables([existing, new_rows])
                    if existing is not None and existing.num_rows
                    else new_rows)

        path = self._write(table, combined)
        note = f", replaced {dropped}" if dropped else ""
        self._log(f"  {label or table}: +{len(rows)} row(s){note} "
                  f"-> {path.name} ({combined.num_rows} total)")
        self.write_manifest()

    # -- the manifest -----------------------------------------------------
    def write_manifest(self):
        """Rebuild `_manifest.json` from the parquet files actually on disk.

        Rebuilt rather than accumulated so the manifest can never name a table
        whose file is absent -- `load_parquet_to_duckdb.py` exits on exactly
        that, and a manifest that outlives its data is a worse failure than a
        short one. Column metadata comes from the contract, so a directory
        holding both a Snowflake dump and local output describes both
        correctly.
        """
        manifest_path = self.parquet_dir / "_manifest.json"
        entries = []
        for table in sorted(self.contract["tables"]):
            path = self.path_for(table)
            if not path.exists():
                continue
            columns = self.columns(table)
            entries.append({
                "table": table,
                "rows": pq.read_metadata(path).num_rows,
                "bytes": path.stat().st_size,
                "variant_columns": [c["name"] for c in columns
                                    if c["snowflake_type"] == "VARIANT"],
                "columns": columns,
            })
        manifest_path.write_text(json.dumps(
            {"source": "espn-extract-local", "tables": entries}, indent=2))
        return manifest_path

    def ensure_contract_tables(self):
        """Give every contract table a parquet file, EMPTY if this extract
        does not populate it. Returns the tables it created.

        MEASURED, not speculative. An ESPN-only local RAW holds 6 of the
        contract's 23 tables, and the dbt project does not degrade gracefully
        to that: the MLB-72 convergence layer reads CBS staging models
        directly, so `dim_owner` and `int_franchise_seasons` fail on a
        missing relation and take a 27-model skip cone with them -- 12 of 41
        selected models built. With the other 17 present but empty, the whole
        project builds 74/74 green. Absent and empty are very different
        answers to "does this install have CBS data", and only one of them
        compiles.

        This is the same move dump_snowflake_raw_to_parquet.py already makes
        for a genuinely empty Snowflake table -- "still emit a file so the
        loader's table list is complete and the DuckDB build sees the same
        relations". Doing it here rather than in load_parquet_to_duckdb.py
        keeps that proven component untouched: it just loads what the
        manifest lists, exactly as before.
        """
        created = []
        for table in sorted(self.contract["tables"]):
            if self.path_for(table).exists():
                continue
            self._write(table, self.schema(table).empty_table())
            created.append(table)
        if created:
            self._log(f"  seeded {len(created)} empty RAW table(s) this "
                      f"extract does not populate, so the DuckDB build sees "
                      f"the full contract: {', '.join(created)}")
        self.write_manifest()
        return created

    # -- the settled-history guard's engine half --------------------------
    def loaded_box_score_periods(self, year, league_key):
        """{matchup_period: last loaded_at} for this league + season.

        The engine-specific half of the MLB-188 guard; the settle/window
        arithmetic and the refusal live in extract.py and are shared by both
        sinks. NOT N/A locally -- more load-bearing than in the warehouse.
        The guard protects per-day club stamps ESPN cannot re-serve, and in a
        Snowflake-free install this parquet file is the only copy of them that
        exists anywhere. The refusal text's "snapshot RAW first" advice has no
        Time Travel behind it here.

        An absent file means nothing has ever been loaded, which is a genuine
        answer. A file that exists and cannot be read is NOT: it propagates,
        and the caller refuses the run (MLB-199 / W-02 -- never infer "absent"
        from an error).
        """
        path = self.path_for("BOX_SCORES")
        if not path.exists():
            return {}

        table = pq.read_table(path, columns=["MATCHUP_PERIOD", "SEASON_YEAR",
                                             "LEAGUE_KEY", "LOADED_AT"])
        season = pc.equal(table["SEASON_YEAR"], pa.scalar(year, pa.int64()))
        # Mirrors the warehouse predicate exactly, legacy NULL arm included.
        # Dead code locally -- this writer always stamps league_key, so no
        # pre-registry rows can exist -- but keeping the two scoping rules
        # textually identical means a future reader diffing the paths finds
        # no difference to explain.
        league = pc.or_(pc.equal(table["LEAGUE_KEY"], pa.scalar(league_key)),
                        pc.is_null(table["LEAGUE_KEY"]))
        table = table.filter(pc.and_(season, league))

        latest = {}
        for mp, loaded_at in zip(table["MATCHUP_PERIOD"].to_pylist(),
                                 table["LOADED_AT"].to_pylist()):
            if mp is None:
                continue
            mp = int(mp)
            if loaded_at is not None and (mp not in latest
                                          or latest[mp] is None
                                          or loaded_at > latest[mp]):
                latest[mp] = loaded_at
            latest.setdefault(mp, loaded_at)
        return latest

    # -- the six writes ---------------------------------------------------
    def write_box_scores(self, records, matchup_period, year, league_key):
        """Replace this (league, season, matchup period) and insert the run's rows.

        The local twin of the warehouse's delete-then-insert, and it carries
        the same warning: the rows this drops hold each player's club as ESPN
        reported it when the period was last written, and ESPN serves only
        CURRENT club. `settled_loaded_periods` is the gate that keeps this
        from being reachable by accident -- do not call this around it.
        """
        loaded_at = datetime.now()
        rows = [{
            "SEASON_YEAR": year,
            "SCORING_PERIOD": record["scoring_period"],
            "MATCHUP_PERIOD": record["matchup_period"],
            "RAW_JSON": to_json_text(record["data"]),
            "LOADED_AT": loaded_at,
            "LEAGUE_KEY": league_key,
        } for record in records]

        def keep(existing):
            same_period = pc.equal(existing["MATCHUP_PERIOD"],
                                   pa.scalar(matchup_period, pa.int64()))
            same_season = pc.equal(existing["SEASON_YEAR"],
                                   pa.scalar(year, pa.int64()))
            same_league = pc.or_(
                pc.equal(existing["LEAGUE_KEY"], pa.scalar(league_key)),
                pc.is_null(existing["LEAGUE_KEY"]))
            in_scope = pc.and_(pc.and_(same_period, same_season), same_league)
            return pc.invert(pc.fill_null(in_scope, False))

        self._append("BOX_SCORES", rows, keep_mask=keep,
                     label=f"BOX_SCORES mp={matchup_period}")

    def _write_snapshot(self, table, payload, year, league_key):
        """One append-only snapshot row. extracted_at is the ordering key
        staging picks 'latest' by, so it is stamped here -- parquet has no
        column defaults to inherit CURRENT_TIMESTAMP() from."""
        self._append(table, [{
            "SEASON_YEAR": year,
            "RAW_JSON": to_json_text(payload),
            "EXTRACTED_AT": datetime.now(),
            "LEAGUE_KEY": league_key,
        }])

    def write_scoring_settings(self, scoring_items, year, league_key):
        self._write_snapshot("SCORING_SETTINGS", scoring_items, year, league_key)

    def write_roster_settings(self, roster_settings, year, league_key):
        self._write_snapshot("ROSTER_SETTINGS", roster_settings, year, league_key)

    def write_team_owners(self, team_owners, year, league_key):
        self._write_snapshot("TEAM_OWNERS", team_owners, year, league_key)

    def write_draft(self, draft_rows, year, league_key):
        self._write_snapshot("DRAFT_PICKS", draft_rows, year, league_key)

    def write_transactions(self, topics, year, league_key):
        self._write_snapshot("TRANSACTIONS", topics, year, league_key)

    # -- deliberately unimplemented ---------------------------------------
    def backfill_club_of_game(self, year, league_key, periods):
        """Not applicable locally, and refused rather than quietly skipped.

        --backfill-club-of-game exists to add clubOfGame to periods written
        BEFORE the field existed (MLB-129). Local RAW has no such era: this
        writer has stamped clubOfGame since its first row. A local run that
        silently no-op'd would look like it had enriched something.
        """
        raise NotImplementedError(
            "--backfill-club-of-game is a warehouse-only path. Local RAW has "
            "carried clubOfGame since its first write, so there is nothing "
            "pre-field to enrich. Run it against --raw-target snowflake."
        )
