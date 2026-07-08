"""One-time RAW migration for the league_key re-grain (MLB-57).

Pre-registry RAW rows have no league_key. Every one of them belongs to
the registry's default league (the ESPN league) by definition -- nothing
else ever wrote to this warehouse. This script:

  1. ALTER TABLE ... ADD COLUMN IF NOT EXISTS league_key VARCHAR on each
     RAW table (idempotent; extract.py also self-heals this, but the
     migration must be runnable before any new extract).
  2. UPDATE ... SET league_key = <default league> WHERE league_key IS NULL.
  3. Prints per-table stamped/remaining-NULL counts so the run is
     auditable.

Idempotent: re-running finds zero NULL rows and changes nothing. Run it
once per warehouse, BEFORE the first post-registry extract (the extract's
scoped DELETE treats NULL rows as the running league's, so ordering only
matters for non-default leagues -- but do it first anyway and the
question never arises).

Usage:
  py tools/migrate_raw_league_key.py --dry-run    # counts only
  py tools/migrate_raw_league_key.py              # stamp NULL rows
"""

import argparse
import os
import sys
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.league_registry import get_league

# The five RAW tables the ESPN extract owns. CBS raw tables (when they
# land under MLB-59+) are created WITH league_key from day one and never
# need this migration.
RAW_TABLES = [
    "BOX_SCORES",
    "SCORING_SETTINGS",
    "ROSTER_SETTINGS",
    "TEAM_OWNERS",
    "DRAFT_PICKS",
]


def build_config():
    """Snowflake connection targeting the RAW landing schema -- the same
    env contract as extract.py (SNOWFLAKE_SCHEMA = raw schema)."""
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


def table_exists(cursor, table):
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = CURRENT_SCHEMA() AND table_name = %s",
        (table,),
    )
    return cursor.fetchone()[0] > 0


def column_exists(cursor, table, column):
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = CURRENT_SCHEMA() AND table_name = %s "
        "AND column_name = %s",
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def main():
    parser = argparse.ArgumentParser(
        description="Stamp pre-registry RAW rows with the default league_key."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report NULL-league_key counts without updating anything.",
    )
    args = parser.parse_args()

    load_dotenv()
    league = get_league()  # registry default: the ESPN league
    league_key = league.key
    print(f"Stamping legacy RAW rows as league_key={league_key!r} "
          f"({league.display_name})")
    if args.dry_run:
        print("DRY RUN -- no rows will be updated.\n")

    with snowflake.connector.connect(**build_config()) as conn:
        cursor = conn.cursor()
        try:
            for table in RAW_TABLES:
                if not table_exists(cursor, table):
                    print(f"  {table:<18} absent -- skipped")
                    continue

                # Dry runs must not mutate anything, including schema: when
                # the column doesn't exist yet, every row needs stamping.
                if not column_exists(cursor, table, "LEAGUE_KEY"):
                    if args.dry_run:
                        cursor.execute(f"SELECT COUNT(*) FROM {table}")
                        total = cursor.fetchone()[0]
                        print(f"  {table:<18} {total:>6} rows, no league_key "
                              f"column yet (all rows need stamping)")
                        continue
                    cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS league_key VARCHAR"
                    )

                cursor.execute(
                    f"SELECT COUNT(*), COUNT_IF(league_key IS NULL) FROM {table}"
                )
                total, null_rows = cursor.fetchone()

                if args.dry_run or null_rows == 0:
                    print(f"  {table:<18} {total:>6} rows, {null_rows} without league_key")
                    continue

                cursor.execute(
                    f"UPDATE {table} SET league_key = %s WHERE league_key IS NULL",
                    (league_key,),
                )
                stamped = cursor.rowcount
                cursor.execute(
                    f"SELECT COUNT_IF(league_key IS NULL) FROM {table}"
                )
                remaining = cursor.fetchone()[0]
                print(f"  {table:<18} {total:>6} rows, stamped {stamped}, "
                      f"{remaining} still NULL")

            if not args.dry_run:
                conn.commit()
                print("\nDone. Committed.")
            else:
                print("\nDry run complete. Nothing changed.")
        finally:
            cursor.close()


if __name__ == "__main__":
    main()
