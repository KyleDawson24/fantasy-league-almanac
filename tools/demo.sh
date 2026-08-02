#!/usr/bin/env bash
#
# demo.sh -- one command from a local DuckDB warehouse to a rendered almanac.
#
# WHAT IT IS FOR. The bring-your-own-league path (MLB-109): a clone with no
# Snowflake account, no Google credentials and no network should still be
# able to see what this project produces. This wraps the two steps that
# takes -- build the chain, render the book -- so neither has to be
# reconstructed from the docs.
#
# WHY IT CAN EXIST AT ALL. Until MLB-10 phase 5 the output layer could only
# talk to Snowflake: `output/db.py` imported the connector at module scope
# and `query_snowflake()` had one destination. Rendering off DuckDB meant a
# scratchpad shim monkey-patching that function, which is a measurement of
# the harness as much as of the code. `db.use_duckdb()` is that switch in
# production, and this script is its user-facing end.
#
# WHAT IT DELIBERATELY DOES NOT DO:
#   * No extract. Landing raw data needs the user's own league credentials
#     and is a separate concern (MLB-11's packaged sample is the answer for
#     people who just want to look).
#   * No Sheets write. Previews are TSV on disk. A demo should not need
#     OAuth, and --no-sheets is what the byte-diff goldens are cut from
#     anyway, so this renders exactly what the tests pin.
#   * No rebuild when a warehouse is already there. A full build is ~20
#     minutes; silently spending that on someone who just wanted a preview
#     is the wrong default. FORCE_BUILD=1 asks for it explicitly.
#
# Usage:   tools/demo.sh                 # default (ESPN) league
#          tools/demo.sh cbs-bsb         # a specific league key
# Tunable: DBT_DUCKDB_PATH OUT_DIR FORCE_BUILD PY_BIN DBT_BIN
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

LEAGUE="${1:-}"
DUCKDB_PATH="${DBT_DUCKDB_PATH:-data/duckdb/ESPN_FANTASY.duckdb}"
OUT_DIR="${OUT_DIR:-out/almanac_preview}"
FORCE_BUILD="${FORCE_BUILD:-0}"

DBT_BIN="${DBT_BIN:-C:/Users/kyled/.venvs/mlb10-duckdb/Scripts/dbt.exe}"
# The interpreter that can see duckdb sits beside the dbt entry point, the
# same way tools/duckdb_run.sh finds it.
PY_BIN="${PY_BIN:-$(dirname "$DBT_BIN")/python.exe}"
[ -x "$PY_BIN" ] || PY_BIN="python"

say() { printf '[demo] %s\n' "$*"; }

say "warehouse : $DUCKDB_PATH"
say "output    : $OUT_DIR"
[ -n "$LEAGUE" ] && say "league    : $LEAGUE"

# ---------------------------------------------------------------------------
# 1. The warehouse.
# ---------------------------------------------------------------------------
if [ "$FORCE_BUILD" = "1" ] || [ ! -f "$DUCKDB_PATH" ]; then
  if [ ! -f "$DUCKDB_PATH" ]; then
    say "no warehouse at that path -- building it (this takes a while)."
  else
    say "FORCE_BUILD=1 -- rebuilding."
  fi
  # Invoked through `bash` rather than as a bare path: that works whether or
  # not the execute bit survived the clone, and the bit is exactly the kind
  # of thing that goes missing between filesystems.
  if ! bash tools/duckdb_run.sh; then
    say "build did not complete; not rendering a half-built warehouse."
    exit 1
  fi
else
  say "warehouse present -- skipping the build (FORCE_BUILD=1 to rebuild)."
fi

# ---------------------------------------------------------------------------
# 2. The render. --duckdb is the whole point: no Snowflake, no network.
# ---------------------------------------------------------------------------
mkdir -p "$OUT_DIR"

RENDER=("$PY_BIN" output/generate_almanac_sheet.py
        --duckdb --no-sheets --preview-dir "$OUT_DIR")
[ -n "$LEAGUE" ] && RENDER+=(--league "$LEAGUE")

say "rendering..."
if ! "${RENDER[@]}"; then
  say "render failed."
  exit 1
fi

# ---------------------------------------------------------------------------
# 3. Say where it went. A demo that leaves the user hunting is not one.
# ---------------------------------------------------------------------------
count=$(find "$OUT_DIR" -maxdepth 1 -name '*.tsv' | wc -l | tr -d ' ')
echo
say "=================== DONE ==================="
say "wrote $count tab(s) to $OUT_DIR"
say "each file is one tab of the almanac, tab-separated."
exit 0
