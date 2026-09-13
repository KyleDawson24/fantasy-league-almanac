"""tools/duckdb_run.sh retries a segfaulting sweep up to MAX_SEGV_RETRIES
times, default 2 (MLB-179).

`stg_mlb__player_game` segfaults nondeterministically on the DuckDB lane
(exit 139; the 2026-09-13 rate trial measured ~40% per attempt over 10
attempts). A segfault writes no run_results.json, so the wrapper used to
stop on its "startup failure" branch and the whole build died on a coin
flip. One retry still left ~16% of builds dead; two leave ~6%, and a crash
dies inside ~2.5 minutes, so the second retry is near-free. These tests
drive the wrapper with a FAKE dbt whose per-call behaviour is scripted, so
no DuckDB, no warehouse and no real dbt are involved -- only the wrapper's
control flow is under test:

  * segv then ok          -> retried once, loudly, and the run reports
                             SUCCESS WITH the retry named in the summary;
  * segv, segv, then ok   -> retried twice and SUCCEEDS, both retries in
                             the summary;
  * segv x3               -> retried twice and then STOPS (exit 1), never
                             loops;
  * MAX_SEGV_RETRIES=1    -> the cap is honoured: segv, segv stops after
                             one retry;
  * plain exit 1          -> NOT retried; only 139 is the flake.

Same bash precondition as tests/test_demo_isolation.py.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "tools" / "duckdb_run.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="duckdb_run.sh needs bash")

FAKE_DBT = r"""#!/usr/bin/env bash
# Scripted stand-in for dbt: line N of $SCRIPT_FILE is call N's behaviour.
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$COUNT_FILE"
mode=$(sed -n "${n}p" "$SCRIPT_FILE")
case "$mode" in
  ok)   mkdir -p "$RESULTS_DIR"
        echo '{"results":[{"unique_id":"model.t.a","status":"success"}]}' > "$RESULTS_DIR/run_results.json"
        exit 0 ;;
  fail) exit 1 ;;
  *)    exit 139 ;;
esac
"""


def run_wrapper(tmp_path: Path, script: list[str],
                extra_env: dict[str, str] | None = None) -> tuple[int, str, int]:
    """Run the wrapper against the fake dbt. Returns (rc, output, dbt calls)."""
    fake = tmp_path / "dbt"
    fake.write_text(FAKE_DBT, encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    (tmp_path / "script.txt").write_text("\n".join(script) + "\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    posix = lambda p: str(p).replace("\\", "/")  # noqa: E731 -- bash-readable

    env = dict(os.environ)
    env.update({
        "DBT_BIN": posix(fake),
        "PROJECT_DIR": posix(proj),
        "TARGET_PATH": "target",
        "COUNT_FILE": posix(tmp_path / "count"),
        "SCRIPT_FILE": posix(tmp_path / "script.txt"),
        "RESULTS_DIR": posix(proj / "target"),
        # The wrapper's JSON helper falls back to `python` on PATH when no
        # interpreter sits beside DBT_BIN; pin it to the one running the tests.
        "PATH": str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
    })
    env.update(extra_env or {})
    proc = subprocess.run(
        [BASH, str(WRAPPER)], cwd=REPO, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    count_file = tmp_path / "count"
    calls = int(count_file.read_text().strip()) if count_file.exists() else 0
    assert calls > 0, "the fake dbt was never invoked -- DBT_BIN did not resolve:\n" + proc.stdout + proc.stderr
    return proc.returncode, proc.stdout + proc.stderr, calls


def test_segfault_is_retried_and_the_success_summary_names_it(tmp_path):
    rc, out, calls = run_wrapper(tmp_path, ["segv", "ok"])
    assert rc == 0, out
    assert calls == 2
    assert "SEGFAULTED (exit 139)" in out
    assert "Retrying the same sweep (retry 1 of 2)" in out
    assert "SUCCESS" in out
    # Loud in the SUMMARY, not just at the moment it happened.
    assert "NOTE: round 1 hit a SEGFAULT (exit 139) and was retried (MLB-179)" in out
    assert "retry 1 exited 0" in out


def test_two_segfaults_are_retried_twice_and_succeed(tmp_path):
    rc, out, calls = run_wrapper(tmp_path, ["segv", "segv", "ok"])
    assert rc == 0, out
    assert calls == 3, "the second retry is what the cap of 2 buys"
    assert "Retrying the same sweep (retry 1 of 2)" in out
    assert "Retrying the same sweep (retry 2 of 2)" in out
    assert "SUCCESS" in out
    # Both retries land in the one summary line, in order.
    assert "retry 1 exited 139; retry 2 exited 0" in out


def test_three_segfaults_stop_after_exactly_two_retries(tmp_path):
    rc, out, calls = run_wrapper(tmp_path, ["segv", "segv", "segv", "ok"])
    assert rc == 1, out
    assert calls == 3, "must retry twice and then stop, never loop"
    assert "again after 2 retry(ies)" in out
    assert "MLB-179" in out
    assert "retry 1 exited 139; retry 2 exited 139" in out


def test_the_cap_is_tunable_down_to_one_retry(tmp_path):
    rc, out, calls = run_wrapper(tmp_path, ["segv", "segv", "ok"],
                                 extra_env={"MAX_SEGV_RETRIES": "1"})
    assert rc == 1, out
    assert calls == 2, "MAX_SEGV_RETRIES=1 must stop after one retry"
    assert "Retrying the same sweep (retry 1 of 1)" in out
    assert "again after 1 retry(ies)" in out


def test_ordinary_failure_is_not_retried(tmp_path):
    rc, out, calls = run_wrapper(tmp_path, ["fail", "ok"])
    assert rc == 1, out
    assert calls == 1, "only exit 139 is the flake; a real failure must not be re-run"
    assert "startup failure" in out
    assert "SEGFAULT" not in out
