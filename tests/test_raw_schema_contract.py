"""Does Snowflake still match the committed RAW type contract? (MLB-208, Act 2)

ONE OF TWO one-sided conformance tests, and the halves must stay apart:

  * THIS file asks "has the WAREHOUSE drifted from the contract?" It needs
    credentials, so it is `warehouse`-marked and excluded from the default
    suite (pytest.ini addopts).
  * tests/test_local_raw_writer.py asks "has the WRITER drifted from the
    contract?" It must run on a stranger's fresh clone with no accounts, no
    credentials and no network.

Collapsing them into one bidirectional check would make the publicly-runnable
half unrunnable, which is the half that matters for the Snowflake-free
on-ramp. They fail for different reasons, on different machines.

A checked-in schema nothing asserts against is just a file that looks
authoritative. This is the assertion; config/raw_schema_contract.json is only
where it points.

Run it:
    pytest tests/test_raw_schema_contract.py -m warehouse
"""

import json
import os
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "config" / "raw_schema_contract.json"

pytestmark = pytest.mark.warehouse


def _missing_credentials():
    """Which credential pieces are absent, or [] if the warehouse is reachable.

    Named rather than counted: a skip that says "no credentials" sends the
    reader to check all five. A skip that says SNOWFLAKE_ACCOUNT sends them
    to the one.
    """
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    missing = [k for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER") if not os.getenv(k)]
    if not (os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH") or os.getenv("SNOWFLAKE_PASSWORD")):
        missing.append("SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_PASSWORD")
    return missing


def _skip_loudly(reason):
    """Skip, but leave a mark that survives a default-format test run.

    A silent skip reads as a pass in a scroll-back, and this codebase has a
    standing rule against checks that appear to have run when they did not.
    The warning shows in pytest's warnings summary without needing -ra, and
    the stderr line shows even when the summary is suppressed.
    """
    banner = f"[SKIPPED - NOT VERIFIED] {reason}"
    warnings.warn(banner, UserWarning, stacklevel=2)
    print(f"\n!! {banner}", file=sys.stderr)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def warehouse_raw_schema():
    """{table: [column dicts]} read live from Snowflake's information_schema."""
    missing = _missing_credentials()
    if missing:
        _skip_loudly(
            f"Snowflake credentials absent ({', '.join(missing)}), so the "
            f"warehouse half of the RAW contract was NOT checked. The "
            f"contract's writer half still runs in the default suite "
            f"(tests/test_local_raw_writer.py)."
        )

    sys.path.insert(0, str(REPO_ROOT))
    try:
        import snowflake.connector
        from tools.dump_snowflake_raw_to_parquet import build_config
        from tools.generate_raw_schema_contract import (
            describe_raw, SOURCE_DATABASE, SOURCE_SCHEMA,
        )
    except ImportError as exc:
        _skip_loudly(f"Snowflake tooling not importable ({exc}); warehouse "
                     f"half of the RAW contract NOT checked.")

    try:
        conn = snowflake.connector.connect(**build_config())
    except Exception as exc:  # noqa: BLE001 - any connect failure is a skip
        _skip_loudly(f"could not reach Snowflake ({type(exc).__name__}: {exc}); "
                     f"warehouse half of the RAW contract NOT checked.")

    cur = conn.cursor()
    try:
        return describe_raw(cur, SOURCE_DATABASE, SOURCE_SCHEMA)
    finally:
        cur.close()
        conn.close()


@pytest.fixture(scope="module")
def contract():
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing. Generate it with "
        f"tools/generate_raw_schema_contract.py."
    )
    return json.loads(CONTRACT_PATH.read_text())


# The contract's own provenance stamp is asserted in the PURE half
# (tests/test_local_raw_writer.py). It reads a committed JSON file and needs
# no warehouse, and this module is `warehouse`-marked at module scope -- so
# putting it here would mean the stamp went unchecked on every fresh clone
# and every default suite run, which is the whole audience for it.


def test_every_contract_table_still_exists(contract, warehouse_raw_schema):
    missing = sorted(set(contract["tables"]) - set(warehouse_raw_schema))
    assert not missing, (
        f"the contract names {len(missing)} RAW table(s) Snowflake no longer "
        f"has: {', '.join(missing)}. Either the warehouse lost them or the "
        f"contract is stale -- resolve before regenerating."
    )


def test_no_warehouse_table_is_missing_from_the_contract(contract, warehouse_raw_schema):
    """A new RAW table nobody regenerated the contract for is drift too.

    Additive drift is the quiet kind: everything keeps working, and the
    contract silently stops describing RAW.
    """
    extra = sorted(set(warehouse_raw_schema) - set(contract["tables"]))
    assert not extra, (
        f"Snowflake has {len(extra)} RAW table(s) the contract does not "
        f"describe: {', '.join(extra)}. Regenerate with "
        f"tools/generate_raw_schema_contract.py and review the diff."
    )


def test_declared_column_types_match_the_contract(contract, warehouse_raw_schema):
    """Name, declared type, precision, scale AND ordinal order, per table.

    Order is asserted because the contract records ordinal_position and the
    parquet writer emits in that order -- and the deployed order is not the
    CREATE TABLE literal's order (BOX_SCORES' DDL lists season_year first;
    its ordinal 1 is SCORING_PERIOD, because the table evolved). A contract
    that got the order wrong would still produce loadable parquet with
    columns in the wrong places.
    """
    problems = []
    for table in sorted(contract["tables"]):
        if table not in warehouse_raw_schema:
            continue  # covered by test_every_contract_table_still_exists
        expected = contract["tables"][table]
        actual = warehouse_raw_schema[table]
        if expected != actual:
            exp_names = [c["name"] for c in expected]
            act_names = [c["name"] for c in actual]
            if exp_names != act_names:
                problems.append(
                    f"{table}: column list/order differs\n"
                    f"    contract:  {exp_names}\n"
                    f"    snowflake: {act_names}"
                )
            else:
                for e, a in zip(expected, actual):
                    if e != a:
                        problems.append(
                            f"{table}.{e['name']}: contract says "
                            f"{e['snowflake_type']}(p={e['precision']},"
                            f"s={e['scale']}), Snowflake says "
                            f"{a['snowflake_type']}(p={a['precision']},"
                            f"s={a['scale']})"
                        )
    assert not problems, (
        "RAW has drifted from the committed contract:\n  "
        + "\n  ".join(problems)
        + "\n\nRegenerate with tools/generate_raw_schema_contract.py and "
          "review the diff before committing -- the contract is what the "
          "local writer and the DuckDB loader both build against."
    )
