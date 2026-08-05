#!/usr/bin/env bash
#
# duckdb_run.sh -- one command to build the whole chain on the DuckDB target.
#
# WHY THIS EXISTS. It was written when the chain would not build in a single
# process at laptop-class caps. With DuckDB's engine thread count actually
# pinned (profiles.yml `settings.threads`), it does: 74/74 in one `dbt run`,
# measured at 1, 2 and 4 threads. The old behaviour was 32 unintended engine
# workers, not a property of the data.
#
# So this is now a SAFETY NET rather than the normal path. It costs one extra
# results-file read on a healthy run and reports "clean" in a single round.
# It earns its keep when something does fail: it re-invokes each failed model
# alone, rebuilds the skip cone, and distinguishes a spill-cap failure (which
# its own process recovers) from a `memory_limit` ceiling (which no number of
# invocations fixes) instead of leaving that to be rediscovered by hand.
#
# WHY IT PARSES run_results.json INSTEAD OF HARD-CODING THE TWO MARTS.
# A hard-coded bridge encodes today's failure set and breaks silently when
# that set changes -- and it changes: at a 4 GB memory_limit the failures are
# four models, not two, and two of them are a class no amount of re-invoking
# can fix (MLB-172, 2026-07-31). Reading the actual results means the wrapper
# reports what really happened, including "this one is a hard ceiling".
#
# TWO FAILURE CLASSES, and the difference is the whole point:
#   max_temp_directory_size -- spill cap. State-dependent: the model fails in
#     company and passes alone, so its own invocation RECOVERS it.
#   memory_limit            -- RAM ceiling. A fresh process resets the buffer
#     pool but not the ceiling, so re-invoking NEVER helps. Reported as a
#     hard ceiling and exits non-zero rather than looping.
#
# It also rebuilds the SKIP cone. dbt marks downstream nodes `skipped`, not
# `error`, so a run that reports 4 errors can have left 38 models unbuilt.
# Escalating only the errors would exit "green" over a half-built warehouse.
#
# The sweep is `dbt run`, matching the profile that was actually measured.
# Escalations are `dbt build`, so a recovered model still gets its tests.
# Overall: models all built; tests run for escalated models only. Set
# SWEEP_CMD=build to test everything, at a cost the published peaks do not
# cover.
#
# Usage:   tools/duckdb_run.sh
# Tunable: DBT_BIN DBT_THREADS DBT_DUCKDB_MEMORY_LIMIT DBT_DUCKDB_TEMP_LIMIT
#          SWEEP_CMD MAX_ROUNDS
#
# NOT `set -e`: a failing dbt invocation is the normal path here.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# A maintainer's venv path is not a default anybody else can use. Fall back to
# whatever `dbt` is on PATH, which is what a fresh clone will have.
DBT_BIN="${DBT_BIN:-C:/Users/kyled/.venvs/mlb10-duckdb/Scripts/dbt.exe}"
command -v "$DBT_BIN" >/dev/null 2>&1 || DBT_BIN="dbt"
PROJECT_DIR="${PROJECT_DIR:-dbt_league}"
PROFILES_DIR="${PROFILES_DIR:-dbt_league/profiles}"
TARGET_PATH="${TARGET_PATH:-target/duckdb}"
DBT_THREADS="${DBT_THREADS:-1}"
SWEEP_CMD="${SWEEP_CMD:-run}"
MAX_ROUNDS="${MAX_ROUNDS:-4}"

export DBT_DUCKDB_MEMORY_LIMIT="${DBT_DUCKDB_MEMORY_LIMIT:-6GB}"
export DBT_DUCKDB_TEMP_LIMIT="${DBT_DUCKDB_TEMP_LIMIT:-6GB}"

RESULTS="$PROJECT_DIR/$TARGET_PATH/run_results.json"

# Python does the JSON: jq is not present on this box, and the DuckDB venv's
# interpreter always is (it sits beside the dbt entry point).
PY_BIN="$(dirname "$DBT_BIN")/python.exe"
[ -x "$PY_BIN" ] || PY_BIN="python"

COMMON=(--project-dir "$PROJECT_DIR" --profiles-dir "$PROFILES_DIR"
        --target-path "$TARGET_PATH" --threads "$DBT_THREADS")

say() { printf '[duckdb_run] %s\n' "$*"; }

# NO floor check here, deliberately. An earlier version warned below ~5.5 GB
# on the strength of a measured "floor" -- but that floor was measured while
# DuckDB was running 32 engine workers, because dbt's `--threads` never
# reached the engine (it sets dbt's model concurrency; the adapter forwards
# the `settings:` block only). A floor made of worker count is not a floor,
# and baking it in here would enshrine a 32-worker number as a property of
# the data. The engine thread count is now pinned in profiles.yml; whatever
# replaces that figure has to be re-measured with it pinned.

parse_results() {   # -> line 1: error models, line 2: skipped models
  "$PY_BIN" - "$RESULTS" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print(""); print(""); raise SystemExit(0)
err, skip = [], []
for r in d.get("results", []):
    uid = r.get("unique_id", "")
    if not uid.startswith("model."):
        continue
    name = uid.split(".")[-1]
    st = r.get("status")
    if st == "error":
        err.append(name)
    elif st == "skipped":
        skip.append(name)
print(" ".join(err))
print(" ".join(skip))
PY
}

classify() {   # $1 = model name -> the cap it hit, read from its own message
  "$PY_BIN" - "$RESULTS" "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("UNKNOWN"); raise SystemExit(0)
target = sys.argv[2]
for r in d.get("results", []):
    if r.get("unique_id", "").split(".")[-1] == target and r.get("status") == "error":
        m = (r.get("message") or "").lower()
        # classify() is only ever called after a model has ALREADY failed in
        # its own process, so the wording must not promise recoverability --
        # that combination means the cap itself is too low, not that the
        # model had noisy neighbours.
        if "max_temp_directory_size" in m:
            print("SPILL CAP (max_temp_directory_size) -- failed even alone, so the cap is too low for it")
        elif "out of memory" in m or "memory_limit" in m:
            print("HARD CEILING (memory_limit) -- re-invoking cannot fix this")
        else:
            print("OTHER -- not a memory failure, read the log")
        break
else:
    print("UNKNOWN")
PY
}

say "caps: memory_limit=$DBT_DUCKDB_MEMORY_LIMIT max_temp_directory_size=$DBT_DUCKDB_TEMP_LIMIT threads=$DBT_THREADS"

sweep_selectors=""          # empty on the first pass = the whole project
declare -A failed_reason=() # model -> why, accumulated across ALL rounds
round=0

while [ "$round" -lt "$MAX_ROUNDS" ]; do
  round=$((round + 1))

  results_before=""
  [ -f "$RESULTS" ] && results_before="$(stat -c %Y "$RESULTS" 2>/dev/null)"

  if [ -z "$sweep_selectors" ]; then
    say "round $round: sweep -- dbt $SWEEP_CMD (whole project)"
    "$DBT_BIN" "$SWEEP_CMD" "${COMMON[@]}"
  else
    # Selectors are SPACE-separated on purpose: dbt reads a COMMA as set
    # INTERSECTION, so `-s a,b` selects nothing and exits 0 -- a green run
    # that built nothing at all.
    say "round $round: rebuilding the skip cone ($(echo "$sweep_selectors" | wc -w) models)"
    # shellcheck disable=SC2086
    "$DBT_BIN" "$SWEEP_CMD" "${COMMON[@]}" -s $sweep_selectors
  fi
  sweep_rc=$?

  # A sweep that dies BEFORE writing run_results.json (compile error, bad
  # profile, missing adapter) leaves the previous run's file in place. Reading
  # it would report the last run's state -- quite possibly "clean" -- over a
  # build that never happened. Refuse to interpret a stale file.
  results_after=""
  [ -f "$RESULTS" ] && results_after="$(stat -c %Y "$RESULTS" 2>/dev/null)"
  # The mtime branch requires a mtime we actually read: on a box without GNU
  # stat both values come back empty, compare equal, and would condemn every
  # normal 72/74 run as a startup failure.
  if [ "$sweep_rc" -ne 0 ]; then
    if [ ! -f "$RESULTS" ]; then
      say "round $round: dbt exited $sweep_rc and $RESULTS does not exist."
      say "That is a startup failure, not a model failure -- read the dbt output above."
      exit 1
    elif [ -n "$results_after" ] && [ "$results_after" = "$results_before" ]; then
      say "round $round: dbt exited $sweep_rc WITHOUT rewriting $RESULTS."
      say "That is a startup failure, not a model failure -- read the dbt output above."
      exit 1
    fi
  fi

  # -t strips \n but not \r, and Python on Windows emits \r\n, so an "empty"
  # line arrives as $'\r' -- which is NOT -z. That silently turned "nothing
  # failed" into "escalate a model named <CR>", four rounds running.
  mapfile -t parsed < <(parse_results)
  errors="${parsed[0]:-}"; errors="${errors//$'\r'/}"
  skips="${parsed[1]:-}";  skips="${skips//$'\r'/}"

  if [ -z "$errors" ] && [ -z "$skips" ]; then
    say "round $round: clean -- no errors, nothing skipped"
    say "SUCCESS: the chain is fully built."
    exit 0
  fi

  say "round $round: $(echo "$errors" | wc -w) error(s), $(echo "$skips" | wc -w) skipped"

  fixed=0
  round_failed=0
  for m in $errors; do
    say "  escalating '$m' into its own process..."
    "$DBT_BIN" build "${COMMON[@]}" -s "$m"
    if [ $? -eq 0 ]; then
      say "  '$m' RECOVERED alone (adjacency, not a ceiling)"
      unset 'failed_reason[$m]'
      fixed=$((fixed + 1))
    else
      reason="$(classify "$m")"; reason="${reason//$'\r'/}"
      say "  '$m' STILL FAILS alone -- $reason"
      # Accumulated across rounds, keyed by model. Resetting this per round
      # dropped round 1's three failures from the final report and announced
      # a single broken model when four were broken -- a build wrapper that
      # under-reports breakage is failing in the worst direction.
      failed_reason[$m]="$reason"
      round_failed=$((round_failed + 1))
    fi
  done

  if [ "$fixed" -eq 0 ] && [ "$round_failed" -gt 0 ]; then
    say "round $round: nothing moved; stopping rather than looping."
    break
  fi

  if [ -z "$skips" ]; then
    if [ "${#failed_reason[@]}" -eq 0 ]; then
      say "SUCCESS: the chain is fully built (via $round round(s))."
      exit 0
    fi
    break
  fi

  sweep_selectors="$skips"
done

echo
say "=================== INCOMPLETE BUILD ==================="
if [ "${#failed_reason[@]}" -gt 0 ]; then
  say "models that fail even in their own process:"
  for m in "${!failed_reason[@]}"; do say "  - $m -- ${failed_reason[$m]}"; done
  say ""
  say "A memory_limit failure is a ceiling, not an adjacency problem --"
  say "raise DBT_DUCKDB_MEMORY_LIMIT or reshape the model. More"
  say "invocations will not help."
else
  say "gave up after $MAX_ROUNDS rounds with work still pending."
  say "raise MAX_ROUNDS if the chain is legitimately deeper than that."
fi
exit 1
