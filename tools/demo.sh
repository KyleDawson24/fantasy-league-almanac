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
# WHICH LEAGUE CONFIG IT READS, and why that is not a detail (MLB-114).
# Seeds come from two roots: dbt_league/seeds/ (reference vocabulary, the
# same for everyone) and a league_config directory chosen by
# DBT_LEAGUE_CONFIG. This script pins that to demo/league_config/ -- the
# fixture, a complete fake league that is tracked in git and contains no
# real-league content. So the demo does not merely "happen to" render fake
# names; it cannot read the real ones, because the directory holding them
# is not on the path it builds from.
#
# It also gets its OWN warehouse, and that is the other half of the same
# guarantee. Rendering reads MARTS, not seeds, so pointing fixture seeds at
# a warehouse whose marts were built from real seeds would still produce a
# real-name book. A separate database file means the demo's marts can only
# ever have been built from the demo's seeds. See the T6 guard below.
#
# Usage:   tools/demo.sh                 # default (ESPN) league
#          tools/demo.sh cbs-bsb         # a specific league key
# Tunable: DBT_DUCKDB_PATH OUT_DIR FORCE_BUILD PY_BIN DBT_BIN
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

LEAGUE="${1:-}"
# The stem must stay ESPN_FANTASY -- DuckDB takes its catalog name from the
# filename and sources.yml pins `database: ESPN_FANTASY`. So the demo warehouse
# is the same NAME in a different DIRECTORY, not a different name.
DUCKDB_PATH="${DBT_DUCKDB_PATH:-data/duckdb/demo/ESPN_FANTASY.duckdb}"
REAL_WAREHOUSE="data/duckdb/ESPN_FANTASY.duckdb"
OUT_DIR="${OUT_DIR:-out/almanac_preview}"
FORCE_BUILD="${FORCE_BUILD:-0}"

# The demo fixture, relative to the dbt project directory (dbt resolves
# seed-paths from there). Exported so every dbt invocation below -- and
# duckdb_run.sh's, which this script calls -- reads the same league config.
export DBT_LEAGUE_CONFIG="${DBT_LEAGUE_CONFIG:-../demo/league_config}"

# A maintainer's venv path is not a default anybody else can use. Fall back to
# whatever `dbt` is on PATH, which is what a fresh clone will have.
DBT_BIN="${DBT_BIN:-C:/Users/kyled/.venvs/mlb10-duckdb/Scripts/dbt.exe}"
command -v "$DBT_BIN" >/dev/null 2>&1 || DBT_BIN="dbt"
# The interpreter that can see duckdb sits beside the dbt entry point, the
# same way tools/duckdb_run.sh finds it.
PY_BIN="${PY_BIN:-$(dirname "$DBT_BIN")/python.exe}"
[ -x "$PY_BIN" ] || PY_BIN="python"

say() { printf '[demo] %s\n' "$*"; }

# ---------------------------------------------------------------------------
# T6 GUARD. The sample workbook this renders is published read-only, so it
# must be the twin render and nothing else. The check is on the PATH rather
# than on the output because by the time a real name is in a TSV it has
# already been written to disk.
#
# Overridable, because "render the demo tabs off my real warehouse" is a
# legitimate thing a maintainer may want -- but it has to be typed out, so
# it can never be what happens by accident.
# ---------------------------------------------------------------------------
if [ "$DUCKDB_PATH" = "$REAL_WAREHOUSE" ] && [ "${DEMO_ALLOW_REAL_WAREHOUSE:-0}" != "1" ]; then
  say "refusing to run: DBT_DUCKDB_PATH points at $REAL_WAREHOUSE."
  say ""
  say "That is the maintainer's warehouse -- its marts are built from real"
  say "league config, so a render off it would carry real names into what is"
  say "meant to be the published sample. The demo builds its own warehouse at"
  say "  data/duckdb/demo/ESPN_FANTASY.duckdb"
  say "Unset DBT_DUCKDB_PATH to use it. If you really do mean to render off"
  say "the real warehouse, set DEMO_ALLOW_REAL_WAREHOUSE=1 -- and do not"
  say "publish the result."
  exit 1
fi

say "warehouse   : $DUCKDB_PATH"
say "league cfg  : $DBT_LEAGUE_CONFIG"
say "output      : $OUT_DIR"
[ -n "$LEAGUE" ] && say "league      : $LEAGUE"

# ---------------------------------------------------------------------------
# 0. Preflight the imports, before spending twenty minutes on a build that
#    cannot be rendered afterwards.
#
#    gspread is here for an unsatisfying reason worth stating plainly: the
#    render path imports it at module scope in several places even though a
#    --no-sheets run never calls the Sheets API. Two of those uses are real
#    at render time (gspread.utils.rowcol_to_a1, building in-sheet formula
#    text), the rest are sink concerns that a lazy import would defer. Until
#    that is untangled, a TSV-only demo still needs the Google Sheets client
#    installed. It needs no Google ACCOUNT and makes no network call.
# ---------------------------------------------------------------------------
missing=""
for mod in duckdb gspread; do
  "$PY_BIN" -c "import $mod" 2>/dev/null || missing="$missing $mod"
done
if [ -n "$missing" ]; then
  say "missing Python package(s):$missing"
  say "  $PY_BIN -m pip install$missing"
  say ""
  say "duckdb reads the local warehouse. gspread is imported by the render"
  say "path even with --no-sheets -- no Google account or network needed,"
  say "but the package has to be importable."
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. The warehouse.
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$DUCKDB_PATH")"

if [ "$FORCE_BUILD" = "1" ] || [ ! -f "$DUCKDB_PATH" ]; then
  if [ ! -f "$DUCKDB_PATH" ]; then
    say "no warehouse at that path -- building it (this takes a while)."
  else
    say "FORCE_BUILD=1 -- rebuilding."
  fi

  # RAW first, and checked BEFORE the seeds so the failure names the thing
  # that is actually missing. The chain transforms raw league data; this
  # script deliberately does not land any (see the header). A clone that has
  # never run an extract has an empty warehouse, and the useful message
  # there is "there is no data yet", not a model error 40 models deep.
  if ! "$PY_BIN" -c "
import sys, duckdb
try:
    c = duckdb.connect(sys.argv[1], read_only=True)
    n = c.execute(\"select count(*) from information_schema.tables where table_schema='RAW'\").fetchone()[0]
except Exception:
    n = 0
sys.exit(0 if n else 1)
" "$DUCKDB_PATH" 2>/dev/null; then
    say "this warehouse has no RAW data, so there is nothing to transform."
    say ""
    say "tools/demo.sh builds and renders; it does not land data. Landing it"
    say "needs either your own league's credentials (SETUP.md) or the packaged"
    say "sample league, which is MLB-11 and not built yet."
    exit 1
  fi

  # Seeds explicitly, because duckdb_run.sh sweeps with `dbt run` and `run`
  # does not load seeds. This is also the step that puts the FIXTURE in the
  # demo warehouse -- DBT_LEAGUE_CONFIG is exported above, so the seed tables
  # can only come from demo/league_config/.
  say "loading the demo fixture seeds..."
  if ! "$DBT_BIN" seed --project-dir dbt_league --profiles-dir dbt_league/profiles \
       --target-path target/duckdb; then
    say "seeding failed; not building on top of it."
    exit 1
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
