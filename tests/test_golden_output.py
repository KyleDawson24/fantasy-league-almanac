"""Golden-output regression tests for Phase 7 rearchitect.

Behavioral safety net: re-runs each output script, strips volatile lines
(log paths, sheets status), and asserts byte-for-byte equality with the
pinned baseline. Catches accidental BBCode drift during the Step 2 dbt
rearchitect (seed expansion, symmetric facts, mart UNPIVOT rewrite).

Marked `warehouse` — skipped by the default `pytest tests/` suite. Run at
checkpoints during Step 2:
    pytest tests/ -m warehouse

To regenerate after an intentional output change:
    REGENERATE_BASELINES=1 pytest tests/ -m warehouse

Baseline tracks "the current MAX(matchup_period)" — the summary script
auto-advances each Sunday, so the fixture is a snapshot of whatever the
latest loaded MP is. Regenerate after each Sunday run; the fixture
filename intentionally drops the week number so we don't have to rename
it weekly. A future v1.x fix would extend the script with a
--matchup-period flag to pin a fixed week and make the test stable
across weekly extracts.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.warehouse

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"


def _normalize(s: str) -> str:
    """Strip volatile lines that vary per-run (log paths with timestamps,
    sheets sink status). Preserves all BBCode content and structure."""
    out = []
    for line in s.splitlines():
        if line.startswith("Log saved to:") or line.startswith("[sheets]"):
            continue
        out.append(line)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def _run_script(*args) -> str:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, (
        f"script failed (exit {proc.returncode}):\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return proc.stdout


def _golden(fixture_name: str, actual: str) -> None:
    fixture = FIX / fixture_name
    if os.environ.get("REGENERATE_BASELINES") == "1":
        fixture.write_text(actual, encoding="utf-8")
        return
    expected = fixture.read_text(encoding="utf-8")
    if actual != expected:
        # Surface a short structural diff hint without dumping the whole
        # 7KB record report into the assertion message.
        exp_lines = expected.splitlines()
        act_lines = actual.splitlines()
        first_diff = next(
            (i for i, (a, e) in enumerate(zip(act_lines, exp_lines)) if a != e),
            min(len(exp_lines), len(act_lines)),
        )
        pytest.fail(
            f"Output drifted from {fixture_name}.\n"
            f"  expected lines: {len(exp_lines)}, actual lines: {len(act_lines)}\n"
            f"  first diff at line {first_diff + 1}:\n"
            f"    expected: {exp_lines[first_diff] if first_diff < len(exp_lines) else '<EOF>'}\n"
            f"    actual:   {act_lines[first_diff] if first_diff < len(act_lines) else '<EOF>'}\n"
            f"If this change is intentional, regenerate:\n"
            f"    REGENERATE_BASELINES=1 pytest tests/ -m warehouse"
        )


def test_summary_bbcode_matches_baseline():
    actual = _normalize(_run_script("output/generate_summary.py"))
    _golden("baseline_summary_current.txt", actual)


def test_records_report_bbcode_matches_baseline():
    actual = _normalize(_run_script("output/generate_records_report.py", "--no-sheets"))
    _golden("baseline_records_report.txt", actual)
