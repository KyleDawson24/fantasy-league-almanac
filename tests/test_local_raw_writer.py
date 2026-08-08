"""Does the local RAW writer still match the committed contract? (MLB-208, Act 4)

THE OTHER HALF of tests/test_raw_schema_contract.py, and the half that has to
run on a stranger's machine: no warehouse, no credentials, no network, no
accounts of any kind. That is the whole point of the ticket -- if this file
needed any of those, the Snowflake-free on-ramp would be unverifiable exactly
where it matters.

Deliberately does NOT import extract/extract.py. That module reads LEAGUE_ID
at import time and raises without it, so a genuine fresh clone cannot import
it; the writer lives in extract/raw_sink.py precisely so this test can reach
it with an empty environment. The shared settle/window arithmetic on top of
the sink is covered in tests/test_extract_club_of_game.py.

The strongest assertion here is the last one: the writer's parquet is handed
to the REAL tools/load_parquet_to_duckdb.py and the resulting DuckDB RAW is
inspected. That proves the contract round-trips through the existing consumer
rather than through a second reader written to agree with the writer.
"""

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Sibling import by bare name, NOT `from extract.raw_sink import ...`. The
# dotted form is not reliable here: `extract/extract.py` puts its own
# directory on sys.path, and once any test has imported it, the name
# `extract` resolves to that MODULE FILE -- a regular module beats a
# namespace-package directory anywhere on the path -- so the dotted import
# passes when this file runs alone and fails in the full suite, depending on
# collection order. The bare name has no such ambiguity.
#
# Done locally rather than in conftest.py on purpose: that file deliberately
# does not put extract/ on the shared path, so the rest of the suite keeps
# its current import surface (see tests/test_extract_club_of_game.py).
sys.path.insert(0, str(REPO_ROOT / "extract"))

from raw_sink import (  # noqa: E402
    ESPN_RAW_TABLES, LocalParquetSink, RawSchemaError, arrow_schema,
    load_contract, to_json_text,
)

CONTRACT_PATH = REPO_ROOT / "config" / "raw_schema_contract.json"
LEAGUE = "espn-main"


# ---------------------------------------------------------------------------
# The contract itself: is it well-formed, and does it say where it came from?
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def contract():
    return load_contract()


def test_contract_records_where_it_came_from(contract):
    """The stamp is the difference between a contract and a plausible file.

    MLB-215's lesson generalized: an artifact that does not record its own
    inputs is a function of whoever ran it, no matter how authoritative it
    looks. Database and schema answer "which catalog was read"; `generator`
    answers "generated, not hand-typed".

    Asserted in the PURE half on purpose -- it reads a committed JSON file and
    needs no warehouse, so putting it behind the warehouse marker would mean
    it never ran on the machines that only ever see the committed artifact.
    """
    assert contract["generator"] == "tools/generate_raw_schema_contract.py"
    assert contract["generated_from"]["database"] == "ESPN_FANTASY"
    assert contract["generated_from"]["schema"] == "RAW"
    assert contract["generated_from"]["catalog"] == "information_schema.columns"
    assert contract["generated_at"], "no generation timestamp"
    assert isinstance(contract["generator_version"], int)


def test_contract_covers_every_table_the_espn_extract_writes(contract):
    missing = [t for t in ESPN_RAW_TABLES if t not in contract["tables"]]
    assert not missing, f"contract is missing ESPN RAW table(s): {missing}"


def test_every_contract_column_is_loadable_by_the_duckdb_loader(contract):
    """The contract must be consumable by the component that already exists.

    tools/load_parquet_to_duckdb.py raises on a type it has no mapping for.
    Asserting here means a future RAW column of a novel type fails in a fast
    pure test naming the column, rather than halfway through a build.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from load_parquet_to_duckdb import duckdb_type

    unmapped = []
    for table, columns in contract["tables"].items():
        for column in columns:
            try:
                duckdb_type(column)
            except ValueError as exc:
                unmapped.append(f"{table}.{column['name']}: {exc}")
    assert not unmapped, "contract types the DuckDB loader cannot map:\n  " + \
                         "\n  ".join(unmapped)


# ---------------------------------------------------------------------------
# The writer against the contract
# ---------------------------------------------------------------------------

@pytest.fixture
def sink(tmp_path):
    return LocalParquetSink(tmp_path / "raw", quiet=True)


def _box_score_records(scoring_periods, matchup_period, note="x"):
    return [{
        "scoring_period": sp,
        "matchup_period": matchup_period,
        "data": {"matchups": [{"home_team": note, "home_lineup": []}],
                 "free_agents": []},
    } for sp in scoring_periods]


def test_written_parquet_matches_the_contract_schema_exactly(sink, contract):
    """Every ESPN table: column names, order and physical types.

    Order is asserted because the contract records the catalog's ordinal
    order, which is NOT the CREATE TABLE literal's order -- BOX_SCORES' DDL
    lists season_year first while its deployed ordinal 1 is SCORING_PERIOD.
    A writer that emitted DDL order would produce a file that loads without
    complaint and puts values in the wrong columns.
    """
    sink.write_box_scores(_box_score_records([1, 2], 1), 1, 2026, LEAGUE)
    sink.write_scoring_settings([{"statId": 1, "points": 1.0}], 2026, LEAGUE)
    sink.write_roster_settings({"lineupSlotCounts": {"0": 1}}, 2026, LEAGUE)
    sink.write_team_owners([{"team_id": 1, "owner_id": "{G}"}], 2026, LEAGUE)
    sink.write_draft([{"overall_pick": 1, "player_id": 7}], 2026, LEAGUE)
    sink.write_transactions([{"id": "t1"}], 2026, LEAGUE)

    for table in ESPN_RAW_TABLES:
        path = sink.path_for(table)
        assert path.exists(), f"{table} was never written"
        actual = pq.read_schema(path)
        expected = arrow_schema(contract["tables"][table])
        assert actual.names == expected.names, (
            f"{table}: column names/order differ\n"
            f"  contract: {expected.names}\n"
            f"  written:  {actual.names}"
        )
        assert actual.types == expected.types, (
            f"{table}: physical types differ\n"
            f"  contract: {expected.types}\n"
            f"  written:  {actual.types}"
        )


def test_league_key_is_stamped_on_every_row(sink):
    """MLB-57: league_key is load metadata every RAW row carries. The local
    writer has to stamp it exactly as the warehouse loaders do, or the
    registry's per-league scoping silently stops working."""
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)
    sink.write_scoring_settings([{"statId": 1}], 2026, LEAGUE)

    for table in ("BOX_SCORES", "SCORING_SETTINGS"):
        keys = pq.read_table(sink.path_for(table))["LEAGUE_KEY"].to_pylist()
        assert keys and all(k == LEAGUE for k in keys), \
            f"{table} rows are not all stamped with the league key: {keys}"


def test_load_timestamps_are_stamped_not_null(sink):
    """Parquet has no column defaults to inherit CURRENT_TIMESTAMP() from.

    extracted_at is the ORDERING KEY the append-only staging models pick the
    latest snapshot by (ROW_NUMBER ... ORDER BY extracted_at DESC). Null it
    and "the latest snapshot" quietly becomes an arbitrary one.
    """
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)
    sink.write_scoring_settings([{"statId": 1}], 2026, LEAGUE)

    loaded = pq.read_table(sink.path_for("BOX_SCORES"))["LOADED_AT"].to_pylist()
    assert all(isinstance(v, datetime) for v in loaded), loaded

    extracted = pq.read_table(
        sink.path_for("SCORING_SETTINGS"))["EXTRACTED_AT"].to_pylist()
    assert all(isinstance(v, datetime) for v in extracted), extracted


def test_variant_payload_round_trips_as_json_text(sink):
    """VARIANT lands as compact JSON text, the same shape the Snowflake dump
    produces via TO_JSON(), because the loader casts that to DuckDB's JSON."""
    payload = [{"statId": 5, "points": 1.5, "isReverseItem": False}]
    sink.write_scoring_settings(payload, 2026, LEAGUE)

    stored = pq.read_table(sink.path_for("SCORING_SETTINGS"))["RAW_JSON"][0].as_py()
    assert json.loads(stored) == payload
    assert stored == to_json_text(payload)
    assert " " not in stored, "VARIANT text should be compact"


# ---------------------------------------------------------------------------
# Append vs overwrite -- each table's warehouse DML, reproduced
# ---------------------------------------------------------------------------

def test_rerunning_a_matchup_period_replaces_it_rather_than_duplicating(sink):
    """BOX_SCORES is delete-then-insert scoped to (season, period, league)."""
    sink.write_box_scores(_box_score_records([1, 2], 1, "first"), 1, 2026, LEAGUE)
    sink.write_box_scores(_box_score_records([1, 2], 1, "second"), 1, 2026, LEAGUE)

    table = pq.read_table(sink.path_for("BOX_SCORES"))
    assert table.num_rows == 2, "re-running a period duplicated its rows"
    assert all("second" in v for v in table["RAW_JSON"].to_pylist()), \
        "the replacement rows did not win"


def test_replacing_one_period_leaves_the_others_alone(sink):
    """The scope is the point: without the period filter a re-run would wipe
    the season, and without the season filter 2025 MP1 would wipe 2026 MP1."""
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)
    sink.write_box_scores(_box_score_records([8], 2), 2, 2026, LEAGUE)
    sink.write_box_scores(_box_score_records([1], 1), 1, 2025, LEAGUE)

    sink.write_box_scores(_box_score_records([1], 1, "redo"), 1, 2026, LEAGUE)

    table = pq.read_table(sink.path_for("BOX_SCORES"))
    assert table.num_rows == 3
    survivors = sorted(zip(table["SEASON_YEAR"].to_pylist(),
                           table["MATCHUP_PERIOD"].to_pylist()))
    assert survivors == [(2025, 1), (2026, 1), (2026, 2)]


def test_a_different_league_is_not_touched(sink):
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, "other-league")
    sink.write_box_scores(_box_score_records([1], 1, "redo"), 1, 2026, LEAGUE)

    table = pq.read_table(sink.path_for("BOX_SCORES"))
    assert sorted(table["LEAGUE_KEY"].to_pylist()) == [LEAGUE, "other-league"]


@pytest.mark.parametrize("write, table", [
    (lambda s, n: s.write_scoring_settings([{"n": n}], 2026, LEAGUE), "SCORING_SETTINGS"),
    (lambda s, n: s.write_roster_settings({"n": n}, 2026, LEAGUE), "ROSTER_SETTINGS"),
    (lambda s, n: s.write_team_owners([{"n": n}], 2026, LEAGUE), "TEAM_OWNERS"),
    (lambda s, n: s.write_draft([{"n": n}], 2026, LEAGUE), "DRAFT_PICKS"),
    (lambda s, n: s.write_transactions([{"n": n}], 2026, LEAGUE), "TRANSACTIONS"),
])
def test_snapshot_tables_are_append_only(sink, write, table):
    """These five keep every snapshot on purpose.

    Collapsing them to overwrite still runs and still returns one row from
    staging, so nothing looks broken -- it just silently discards the
    history the ELT design keeps. That is why this is asserted per table
    rather than assumed from the shared code path.
    """
    write(sink, 1)
    write(sink, 2)
    write(sink, 3)
    assert pq.read_table(sink.path_for(table)).num_rows == 3


# ---------------------------------------------------------------------------
# The MLB-188 guard's local half
# ---------------------------------------------------------------------------

def test_guard_reports_nothing_loaded_when_no_parquet_exists(sink):
    """A first extract invents no history. An ABSENT file is a real answer."""
    assert sink.loaded_box_score_periods(2026, LEAGUE) == {}


def test_guard_reads_back_what_the_writer_wrote(sink):
    sink.write_box_scores(_box_score_records([1, 2], 1), 1, 2026, LEAGUE)
    sink.write_box_scores(_box_score_records([8], 2), 2, 2026, LEAGUE)

    loaded = sink.loaded_box_score_periods(2026, LEAGUE)
    assert sorted(loaded) == [1, 2]
    assert all(isinstance(v, datetime) for v in loaded.values())


def test_guard_scopes_to_season_and_league(sink):
    sink.write_box_scores(_box_score_records([1], 1), 1, 2025, LEAGUE)
    sink.write_box_scores(_box_score_records([1], 3), 3, 2026, "other-league")

    assert sink.loaded_box_score_periods(2026, LEAGUE) == {}
    assert sorted(sink.loaded_box_score_periods(2025, LEAGUE)) == [1]


def test_guard_propagates_rather_than_reporting_empty_when_unreadable(sink):
    """MLB-199 / W-02, in its local form. A file that exists and cannot be
    read must NOT answer "nothing is loaded" -- the caller turns any raised
    error into a refusal, and that only works if the error is raised."""
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)
    sink.path_for("BOX_SCORES").write_bytes(b"not a parquet file")

    with pytest.raises(Exception) as excinfo:
        sink.loaded_box_score_periods(2026, LEAGUE)
    assert not isinstance(excinfo.value, AssertionError)


# ---------------------------------------------------------------------------
# The manifest, and refusals
# ---------------------------------------------------------------------------

def test_manifest_matches_what_the_duckdb_loader_consumes(sink, contract):
    sink.write_box_scores(_box_score_records([1, 2], 1), 1, 2026, LEAGUE)
    manifest = json.loads((sink.parquet_dir / "_manifest.json").read_text())

    assert set(manifest) == {"source", "tables"}
    entry, = [e for e in manifest["tables"] if e["table"] == "BOX_SCORES"]
    assert set(entry) == {"table", "rows", "bytes", "variant_columns", "columns"}
    assert entry["rows"] == 2
    assert entry["variant_columns"] == ["RAW_JSON"]
    assert entry["columns"] == contract["tables"]["BOX_SCORES"], \
        "manifest column metadata must come from the contract verbatim"


def test_manifest_only_names_tables_whose_parquet_exists(sink):
    """load_parquet_to_duckdb.py exits if the manifest names a missing file,
    so a manifest that outlives its data is worse than a short one."""
    sink.write_scoring_settings([{"statId": 1}], 2026, LEAGUE)
    manifest = json.loads((sink.parquet_dir / "_manifest.json").read_text())

    named = {e["table"] for e in manifest["tables"]}
    assert named == {"SCORING_SETTINGS"}
    for entry in manifest["tables"]:
        assert (sink.parquet_dir / f"{entry['table']}.parquet").exists()


def test_unpopulated_contract_tables_are_created_empty(sink, contract):
    """Absent and empty are different answers, and only one of them builds.

    An ESPN-only local RAW holds 6 of the contract's 23 tables. The MLB-72
    convergence layer reads CBS staging directly, so with the other 17
    ABSENT, dim_owner and int_franchise_seasons fail on a missing relation
    and drag a 27-model skip cone with them. Present-but-empty builds the
    whole project green. Measured, not assumed.
    """
    created = sink.ensure_contract_tables()
    assert set(created) == set(contract["tables"]), \
        "a fresh landing area should materialize every contract table"

    for table in contract["tables"]:
        path = sink.path_for(table)
        assert path.exists(), f"{table} was not created"
        assert pq.read_metadata(path).num_rows == 0
        assert pq.read_schema(path).names == \
            [c["name"] for c in contract["tables"][table]]


def test_ensuring_tables_never_disturbs_existing_data(sink):
    """It must be safe to call on every run, including over real rows."""
    sink.write_box_scores(_box_score_records([1, 2], 1), 1, 2026, LEAGUE)
    created = sink.ensure_contract_tables()

    assert "BOX_SCORES" not in created
    assert pq.read_table(sink.path_for("BOX_SCORES")).num_rows == 2


def test_writing_after_ensuring_appends_to_the_empty_table(sink):
    """The empty file must be a real typed table the writer can append to,
    not a placeholder that has to be special-cased later."""
    sink.ensure_contract_tables()
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)

    table = pq.read_table(sink.path_for("BOX_SCORES"))
    assert table.num_rows == 1
    assert table["LEAGUE_KEY"].to_pylist() == [LEAGUE]


def test_no_partial_file_survives_a_write(sink):
    """Writes go to .partial and are renamed, so an interrupted run cannot
    leave a half-written file the next run treats as good."""
    sink.write_box_scores(_box_score_records([1], 1), 1, 2026, LEAGUE)
    assert not list(sink.parquet_dir.glob("*.partial"))


def test_backfill_is_refused_locally_rather_than_silently_skipped(sink):
    """A no-op would look like it had enriched something."""
    with pytest.raises(NotImplementedError) as excinfo:
        sink.backfill_club_of_game(2026, LEAGUE, [1])
    assert "warehouse-only" in str(excinfo.value)


def test_unknown_table_is_refused(sink):
    with pytest.raises(RawSchemaError):
        sink.columns("NO_SUCH_TABLE")


# ---------------------------------------------------------------------------
# The round trip, through the real consumer
# ---------------------------------------------------------------------------

def test_written_parquet_loads_into_duckdb_with_contract_types(sink, tmp_path):
    """Writer -> parquet -> the REAL loader -> DuckDB, with no warehouse.

    This is the acceptance sentence's middle two arrows, executed. It uses
    tools/load_parquet_to_duckdb.py unchanged and unmocked, so what is proven
    is that the local writer produces artifacts the EXISTING consumer accepts
    -- not that two things I wrote agree with each other.
    """
    import duckdb

    sink.write_box_scores(_box_score_records([1, 2], 1), 1, 2026, LEAGUE)
    sink.write_scoring_settings([{"statId": 1, "points": 1.0}], 2026, LEAGUE)

    db_path = tmp_path / "ESPN_FANTASY.duckdb"
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "load_parquet_to_duckdb.py"),
         "--parquet-dir", str(sink.parquet_dir),
         "--db", str(db_path),
         "--memory-limit", "1GB"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"the DuckDB loader rejected the local writer's output:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "row counts match the dump manifest" in result.stdout

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        types = dict(con.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema = 'RAW' and table_name = 'BOX_SCORES'"
        ).fetchall())
        rows = con.execute("select count(*) from RAW.BOX_SCORES").fetchone()[0]
        # RAW_JSON must arrive as JSON, not text: the staging models parse it.
        payload = con.execute(
            "select raw_json -> '$.matchups[0].home_team' from RAW.BOX_SCORES "
            "limit 1").fetchone()[0]
    finally:
        con.close()

    assert rows == 2
    assert types["RAW_JSON"] == "JSON"
    assert types["LEAGUE_KEY"] == "VARCHAR"
    assert types["LOADED_AT"] == "TIMESTAMP"
    # NUMBER(38,0) -> DECIMAL(38,0): the loader's precision guard, and what
    # the deployed warehouse-fed database holds too.
    assert types["SEASON_YEAR"] == "DECIMAL(38,0)"
    assert json.loads(payload) == "x"
